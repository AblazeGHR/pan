import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from 'react';
import { createPortal } from 'react-dom';
import { Loader2, UserRound } from 'lucide-react';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { useSessionStore } from '@/stores/sessionStore';
import { canonicalRowsWithOffsets } from '@/stores/messageOrdering';
import { fetchSessionNavigation } from '@/services/api';
import {
    type QuickJumpKind,
} from './messageFilter';
import { NavigationIndex, NAVIGATION_PAGE_SIZE, nearestNavigationTarget, type NavigationRange, type NavigationTarget } from './navigationIndex';
import type { ChatMessagesHandle } from './ChatMessages';

interface MessageNavigationRailProps {
  chatRef: RefObject<ChatMessagesHandle | null>;
  expanded?: boolean;
  isMobile?: boolean;
  mobileExpanded?: boolean;
}

type IndexStatus = 'idle' | 'loading' | 'ready' | 'error';

const USER_LABEL = '\u7528\u6237';
const RAIL_LABEL = '\u5feb\u901f\u5b9a\u4f4d';
const PREVIEW_FALLBACK = '\u65e0\u9884\u89c8\u5185\u5bb9';
const LONG_PRESS_MS = 450;
const PRE_LONG_PRESS_MOVE_PX = 10;

interface ScrubGesture {
  pointerId: number;
  startX: number;
  startY: number;
  fromEnd: number;
  active: boolean;
  moved: boolean;
  timer: number | null;
}

type PreviewMode = 'hover' | 'scrub';

/**
 * The single rail column shows every navigable kind in the session's original
 * message order. Classification belongs to the shared `./messageFilter`
 * module; the two MA ids below are the Rail-side consumption proposal
 * (reported for coordination) and light up unchanged once the classifier
 * emits them — the Rail never classifies by itself.
 */
type RailNavigationKind = QuickJumpKind | 'maAssign' | 'maMsg';

interface NavigationKindMeta {
  /** Tooltip heading and marker aria label for this kind. */
  label: string;
  /** Compact text glyph used when the kind has no dedicated icon. */
  tag?: string;
  /** Marker style suffix: `message-navigation-marker-<slug>`. */
  slug: string;
}

const KIND_META: Record<RailNavigationKind, NavigationKindMeta> = {
  user: { label: USER_LABEL, slug: 'user' },
  worker: { label: 'TA report', tag: 'Re', slug: 'worker' },
  system: { label: 'System', tag: 'Sys', slug: 'system' },
  maAssign: { label: 'MA assign', tag: 'MA', slug: 'ma-assign' },
  maMsg: { label: 'MA msg', tag: 'MA', slug: 'ma-msg' },
};

function MarkerGlyph({ kind }: { kind: RailNavigationKind }) {
  const { tag } = KIND_META[kind];
  return tag
    ? <span className="message-navigation-marker-tag" aria-hidden="true">{tag}</span>
    : <UserRound size={13} strokeWidth={2.4} />;
}

function nextPaint(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
}

export function MessageNavigationRail({
  chatRef,
  expanded = true,
  isMobile = false,
  mobileExpanded = false,
}: MessageNavigationRailProps) {
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const currentHistoryEpoch = useSessionStore((s) => s.currentSessionId
    ? s.sessionTranscripts[s.currentSessionId]?.window.epoch ?? null : null);
  const historyLoadEnd = useSessionStore((s) => s.historyLoadEnd);
  const currentHistoryTotal = useSessionStore((s) => {
    const session = s.sessions.find((item) => item.id === s.currentSessionId);
    return session?.historyTotal ?? null;
  });
  const ensureMessageLoaded = useSessionStore((s) => s.ensureMessageLoaded);
  const showMetaAgent = useAppSettingsStore((s) => s.showMetaAgent);
  const showTaskAgent = useAppSettingsStore((s) => s.showTaskAgent);
  const showQQ = useAppSettingsStore((s) => s.showQQ);
  const settings = useMemo(
    () => ({ showMetaAgent, showTaskAgent, showQQ }),
    [showMetaAgent, showTaskAgent, showQQ],
  );
  const [activeOffset, setActiveOffset] = useState<number | null>(null);
  const [jumpingOffset, setJumpingOffset] = useState<number | null>(null);
  const [jumpError, setJumpError] = useState<string | null>(null);
  const [hovered, setHovered] = useState<{
    target: NavigationTarget;
    top: number;
    left: number;
    mode: PreviewMode;
  } | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const scrubGestureRef = useRef<ScrubGesture | null>(null);
  const suppressNextClickRef = useRef(false);
  const loadedCanonicalTotal = currentHistoryTotal ?? historyLoadEnd;
  const context = useMemo(() => {
    const state = useSessionStore.getState();
    const total = state.sessions.find(session => session.id === currentSessionId)?.historyTotal;
    return { sessionId: currentSessionId, historyEpoch: currentHistoryEpoch, settings,
      responseEpoch: (currentHistoryEpoch ?? undefined) as string | null | undefined,
      knownTotal: total !== undefined && total !== null, index: new NavigationIndex(total ?? state.historyLoadEnd, settings) };
  }, [currentSessionId, currentHistoryEpoch, settings]);
  const contextRef = useRef(context);
  contextRef.current = context;
  const [indexView, setIndexView] = useState<{ context: typeof context; targets: NavigationTarget[] } | null>(null);
  const targets = useMemo(() => indexView?.context === context ? indexView.targets : [], [indexView,context]);
  const [indexState, setIndexState] = useState<{ context: typeof context; status: IndexStatus; error: string | null } | null>(null);
  const indexStatus = indexState?.context === context ? indexState.status : 'idle';
  const indexError = indexState?.context === context ? indexState.error : null;
  const [indexMetrics, setIndexMetrics] = useState({ requests: 0, durationMs: 0 });
  const indexTotal = context.index.total;
  const targetsByFromEnd = useMemo(() => new Map(targets.map(target => [target.fromEnd, target])), [targets]);
  const openEpochRef = useRef(0);
  const operationRef = useRef<{ controller: AbortController; context: typeof context } | null>(null);
  const pendingOpenRef = useRef<{ epoch: number; context: typeof context; range: NavigationRange | null } | null>(null);
  const observerCleanupRef = useRef<(() => void) | null>(null);
  const [viewportTarget, setViewportTarget] = useState<{ context: typeof context; epoch: number; offset: number } | null>(null);
  const [retryEpoch, setRetryEpoch] = useState(0);
  const [listScroll, setListScroll] = useState(0);
  const rowHeight = isMobile ? 45 : 27;
  const previousListRef = useRef<{ context: typeof context; targets: NavigationTarget[] }>({context,targets:[]});
  const targetsRef = useRef(targets); targetsRef.current = targets;
  const continuingRef = useRef(false);
  const pendingWantedRef = useRef(true);
  const locationRef = useRef<{ context: typeof context; range: NavigationRange } | null>(null);
  const appendRef = useRef({ context, total: loadedCanonicalTotal });
  const clearScrub = useCallback((suppressClick = false) => {
    const gesture = scrubGestureRef.current;
    if (gesture?.timer !== null && gesture?.timer !== undefined) window.clearTimeout(gesture.timer);
    scrubGestureRef.current = null;
    if (suppressClick && gesture && (gesture.active || gesture.moved)) suppressNextClickRef.current = true;
    setHovered(null);
  }, []);

  const cancelOpenLocation = useCallback(() => {
    pendingOpenRef.current = null;
    observerCleanupRef.current?.(); observerCleanupRef.current = null;
  }, []);

  useLayoutEffect(() => {
    if (loadedCanonicalTotal > context.index.total) context.index.total = loadedCanonicalTotal;
    const previous=appendRef.current;
    appendRef.current={context,total:loadedCanonicalTotal};
    if(previous.context===context&&loadedCanonicalTotal>previous.total) {
      setIndexView(value=>value?.context===context?{context,targets:context.index.targets()}:value);
      // Continue compact coverage for an append, preserving the open snapshot.
      continuingRef.current=true;setRetryEpoch(value=>value+1);
    }
  },[loadedCanonicalTotal,context]);
  useLayoutEffect(()=>{
    clearScrub(false); setJumpError(null);setActiveOffset(null);setJumpingOffset(null);
  },[context,clearScrub]);

  useLayoutEffect(() => {
    const wanted=pendingWantedRef.current;
    cancelOpenLocation();
    const epoch=++openEpochRef.current;
    operationRef.current?.controller.abort(); operationRef.current=null;
    if(!expanded||!context.sessionId)return;
    const controller=new AbortController();
    const operation={controller,context};operationRef.current=operation;
    const pending={epoch,context,range:null as NavigationRange|null};
    pendingOpenRef.current=pending;
    // Retrying/append continues the same snapshot. Actual reopen re-snapshots.
    const retrying=continuingRef.current&&locationRef.current?.context===context;continuingRef.current=false;
    pendingWantedRef.current=retrying?wanted:true;
    if(!pendingWantedRef.current)pendingOpenRef.current=null;
    const current=()=>!controller.signal.aborted&&contextRef.current===context
      &&operationRef.current===operation&&useSessionStore.getState().currentSessionId===context.sessionId;
    let requests=0;
    const startedAt=performance.now();
    const publish=()=>{if(current())setIndexView({context,targets:context.index.targets()});};
    const locate=()=>{
      if(pendingOpenRef.current!==pending||!pending.range||!current()||!context.index.proven(pending.range))return;
      const best=nearestNavigationTarget(context.index.targets(),pending.range);
      pendingOpenRef.current=null;pendingWantedRef.current=false;
      if(best)setViewportTarget({context,epoch,offset:best.offset});
    };
    const acceptRange=(range:NavigationRange)=>{
      if(!current()||pendingOpenRef.current!==pending||pending.range!==null)return;
      if(!Number.isSafeInteger(range.start)||!Number.isSafeInteger(range.end)||range.start<0||range.end<range.start)return;
      pending.range=range;locationRef.current={context,range};
      observerCleanupRef.current?.();observerCleanupRef.current=null;
      const state=useSessionStore.getState();
      const transcript=state.sessionTranscripts[context.sessionId!];
      const center=(range.start+range.end)/2;
      const rows=[];
      if(transcript){
        for(let offset=Math.max(0,Math.floor(center-1000));offset<=Math.min(context.index.total-1,Math.ceil(center+1000));offset++){
          const message=transcript.window.rows.get(offset);if(message)rows.push({offset,message});
        }
      }else rows.push(...canonicalRowsWithOffsets(state.currentMessages).filter(row=>Math.abs(row.offset-center)<=1000));
      context.index.addRows(rows);publish();locate();
    };
    setViewportTarget(value=>retrying&&value?.context===context?value:null);
    const range=retrying?locationRef.current!.range:chatRef.current?.getViewportHistoryRange?.();
    if(range)acceptRange(range);
    else if(chatRef.current?.observeViewportHistoryRange)observerCleanupRef.current=chatRef.current.observeViewportHistoryRange(acceptRange);
    const pause=(ms:number)=>new Promise<void>((resolve,reject)=>{
      const abort=()=>{clearTimeout(timer);reject(new DOMException('Aborted','AbortError'));};
      const timer=window.setTimeout(()=>{controller.signal.removeEventListener('abort',abort);resolve();},ms);
      controller.signal.addEventListener('abort',abort,{once:true});
    });
    const run=async()=>{
      if(context.knownTotal&&context.index.complete()){
        publish();locate();setIndexState({context,status:'ready',error:null});return;
      }
      setIndexState({context,status:'loading',error:null});
      try{
        while(current()){
          const missing=context.knownTotal?context.index.nextMissing(pendingOpenRef.current===pending?pending.range??undefined:undefined):0;
          if(missing===null)break;
          const pageSize=pendingOpenRef.current===pending&&pending.range?NAVIGATION_PAGE_SIZE:500;
          const before=context.knownTotal?Math.min(context.index.total,(Math.floor(missing/pageSize)+1)*pageSize):0;
          let page:Awaited<ReturnType<typeof fetchSessionNavigation>>|undefined;
          for(let attempt=0;attempt<3&&current();attempt++){
            try{requests++;page=await fetchSessionNavigation(context.sessionId!,before,pageSize,controller.signal);break;}
            catch(error){
              if(!current())return;
              const code=error instanceof Error&&'status' in error?Number(error.status):0;
              if(attempt===2||(code>=400&&code<500&&code!==408&&code!==429))throw error;
              await pause(attempt===0?150:450);
            }
          }
          if(!current()||!page)return;
          const responseEpoch=page.historyEpoch??null;
          if(context.responseEpoch===undefined)context.responseEpoch=responseEpoch;
          if(responseEpoch!==context.responseEpoch)throw new Error('Navigation history identity changed. Refresh the chat before retrying.');
          if(context.knownTotal&&page.total<context.index.total)throw new Error('Navigation history total regressed. Refresh the chat.');
          context.index.total=page.total;context.knownTotal=true;
          const end=before===0?page.total:before;
          if(page.start!==Math.max(0,end-pageSize)||page.history.length!==end-page.start)throw new Error('Navigation canonical coverage is incomplete.');
          context.index.addPage(page.history,page.start);publish();locate();
          setIndexMetrics({requests,durationMs:performance.now()-startedAt});
          // Yield between compact batches; no fixed startup delay or polling.
          if(!context.index.complete())await new Promise<void>(resolve=>requestAnimationFrame(()=>resolve()));
        }
        if(current())setIndexState({context,status:'ready',error:null});
      }catch(error){
        if(current()){
          const message=error instanceof Error?error.message:String(error);
          console.warn('[message-navigation] compact index failed',{sessionId:context.sessionId,requests,error:message});
          setIndexState({context,status:'error',error:message});
        }
      }finally{if(current())setIndexMetrics({requests,durationMs:performance.now()-startedAt});}
    };
    void run();
    const input=(event:Event)=>{
      if(event.target instanceof Element&&event.target.closest('[data-navigation-load]'))return;
      // Reading cancels only the one-shot location. Complete indexing continues
      // and preserves the user's list anchor, with no scroll-driven requests.
      pendingWantedRef.current=false;cancelOpenLocation();
    };
    for(const type of ['pointerdown','wheel','keydown','touchmove'])window.addEventListener(type,input,true);
    return()=>{
      cancelOpenLocation();controller.abort();
      if(operationRef.current===operation)operationRef.current=null;
      for(const type of ['pointerdown','wheel','keydown','touchmove'])window.removeEventListener(type,input,true);
    };
  },[expanded,context,chatRef,retryEpoch,cancelOpenLocation]);

  useLayoutEffect(()=>{
    const list=listRef.current;
    const previous=previousListRef.current;
    previousListRef.current={context,targets};
    if(!list||previous.context!==context||previous.targets===targets||!previous.targets.length)return;
    const ordinal=Math.min(previous.targets.length-1,Math.floor(Math.max(0,list.scrollTop-8)/rowHeight));
    const anchor=previous.targets[ordinal];
    const next=targets.findIndex(target=>target.offset===anchor?.offset);
    if(next>=0){list.scrollTop+=(next-ordinal)*rowHeight;setListScroll(list.scrollTop);}
  },[targets,context,rowHeight]);
  useLayoutEffect(()=>{
    if(!viewportTarget||viewportTarget.context!==context||viewportTarget.epoch!==openEpochRef.current||!expanded)return;
    const list=listRef.current;
    const ordinal=targetsRef.current.findIndex(target=>target.offset===viewportTarget.offset);
    if(!list||ordinal<0)return;
    list.scrollTop=Math.max(0,8+(ordinal+0.5)*rowHeight-list.clientHeight/2);setListScroll(list.scrollTop);
    // targets changes preserve anchors above; they must never repeat location.
  },[viewportTarget,context,expanded,rowHeight]);
  const retryIndex=()=>{
    if(!expanded||indexStatus==='loading')return;
    continuingRef.current=true;setRetryEpoch(value=>value+1);
  };

  const jumpTo = async (target: NavigationTarget) => {
    if (!currentSessionId || jumpingOffset !== null) return;
    const contextAtClick = context;
    pendingWantedRef.current=false;cancelOpenLocation(); setViewportTarget(null); setJumpError(null); setJumpingOffset(target.offset);
    const current = () => contextRef.current === contextAtClick
      && useSessionStore.getState().currentSessionId === contextAtClick.sessionId;
    try {
      const message = await ensureMessageLoaded(context.index.total - 1 - target.offset, context.index.total);
      if (!current()) return;
      if (!message || (target.messageId && message.messageId !== target.messageId)) {
        setJumpError('Unable to load this canonical message.'); return;
      }
      await nextPaint();
      if (!current()) return;
      let didJump = chatRef.current?.scrollToMessage(message, target.offset) ?? false;
      if (!didJump) {
        await nextPaint(); if (!current()) return;
        didJump = chatRef.current?.scrollToMessage(message, target.offset) ?? false;
      }
      if (!didJump) { setJumpError('Message loaded, but its rendered row could not be located.'); return; }
      setActiveOffset(target.offset);
      const epochAtJump = openEpochRef.current;
      window.setTimeout(() => { if (current() && epochAtJump === openEpochRef.current) setActiveOffset(null); }, 1200);
    } finally { if (current()) setJumpingOffset(null); }
  };

  const setMarkerPreview = useCallback((element: HTMLElement, target: NavigationTarget, mode: PreviewMode) => {
    const marker = element.getBoundingClientRect();
    setHovered({ target, top: marker.top + marker.height / 2, left: marker.left - 9, mode });
  }, []);

  const showPreview = (event: MouseEvent<HTMLButtonElement>, target: NavigationTarget) => {
    setMarkerPreview(event.currentTarget, target, 'hover');
  };

  const updateScrubPreview = useCallback((x: number, y: number, fallback: EventTarget | null) => {
    const gesture = scrubGestureRef.current;
    const list = listRef.current;
    if (!gesture?.active || !list) return;

    const bounds = list.getBoundingClientRect();
    if (
      bounds.width > 0 && bounds.height > 0 &&
      (x < bounds.left || x > bounds.right || y < bounds.top || y > bounds.bottom)
    ) {
      clearScrub(true);
      return;
    }

    const pointTarget = (typeof document.elementFromPoint === 'function'
      ? document.elementFromPoint(x, y)
      : null) ?? fallback;
    const marker = pointTarget instanceof Element
      ? pointTarget.closest<HTMLButtonElement>('.message-navigation-marker')
      : null;
    if (!marker || !list.contains(marker)) return;

    const fromEnd = Number(marker.dataset.fromEnd);
    if (!Number.isInteger(fromEnd) || fromEnd === gesture.fromEnd) return;
    const target = targetsByFromEnd.get(fromEnd);
    if (!target) return;
    gesture.fromEnd = fromEnd;
    setMarkerPreview(marker, target, 'scrub');
  }, [clearScrub, setMarkerPreview, targetsByFromEnd]);

  const beginScrub = (event: ReactPointerEvent<HTMLButtonElement>, target: NavigationTarget) => {
    if (
      !isMobile || !mobileExpanded ||
      (event.pointerType && event.pointerType !== 'touch')
    ) return;
    clearScrub(false);
    suppressNextClickRef.current = false;
    const marker = event.currentTarget;
    const gesture: ScrubGesture = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      fromEnd: target.fromEnd,
      active: false,
      moved: false,
      timer: null,
    };
    gesture.timer = window.setTimeout(() => {
      if (
        scrubGestureRef.current !== gesture ||
        useSessionStore.getState().currentSessionId !== currentSessionId
      ) return;
      gesture.active = true;
      suppressNextClickRef.current = true;
      setMarkerPreview(marker, target, 'scrub');
    }, LONG_PRESS_MS);
    scrubGestureRef.current = gesture;
  };

  const moveScrub = (event: ReactPointerEvent<HTMLDivElement>) => {
    const gesture = scrubGestureRef.current;
    if (!gesture || (Number.isFinite(event.pointerId) && gesture.pointerId !== event.pointerId)) return;
    if (!gesture.active) {
      if (Math.hypot(event.clientX - gesture.startX, event.clientY - gesture.startY) > PRE_LONG_PRESS_MOVE_PX) {
        if (gesture.timer !== null) window.clearTimeout(gesture.timer);
        gesture.timer = null;
        gesture.moved = true;
      }
      return;
    }
    updateScrubPreview(event.clientX, event.clientY, event.target);
  };

  const finishScrub = (event: ReactPointerEvent<HTMLDivElement>, cancelled: boolean) => {
    const gesture = scrubGestureRef.current;
    if (!gesture || (Number.isFinite(event.pointerId) && gesture.pointerId !== event.pointerId)) return;
    clearScrub(!cancelled && (gesture.active || gesture.moved));
  };

  const handleMarkerClick = (event: MouseEvent<HTMLButtonElement>, target: NavigationTarget) => {
    if (suppressNextClickRef.current && event.detail !== 0) {
      suppressNextClickRef.current = false;
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    void jumpTo(target);
  };

  useEffect(() => {
    if (!isMobile || !mobileExpanded) {
      clearScrub(true);
      return;
    }
    const list = listRef.current;
    if (!list) return;
    const handleTouchMove = (event: globalThis.TouchEvent) => {
      if (!scrubGestureRef.current?.active) return;
      const touch = event.changedTouches[0] ?? event.touches[0];
      if (!touch) return;
      if (event.cancelable) event.preventDefault();
      updateScrubPreview(touch.clientX, touch.clientY, event.target);
    };
    const handleTouchCancel = () => clearScrub(false);
    list.addEventListener('touchmove', handleTouchMove, { passive: false });
    list.addEventListener('touchcancel', handleTouchCancel);
    return () => {
      list.removeEventListener('touchmove', handleTouchMove);
      list.removeEventListener('touchcancel', handleTouchCancel);
    };
  }, [clearScrub, isMobile, mobileExpanded, updateScrubPreview]);

  const virtualStart=Math.max(0,Math.floor(Math.max(0,listScroll-8)/rowHeight)-12);
  const virtualEnd=Math.min(targets.length,virtualStart+Math.max(60,Math.ceil((listRef.current?.clientHeight??700)/rowHeight)+24));
  const displayTargets=targets.slice(virtualStart,virtualEnd);
  return (
    <aside
      className="message-navigation-rail"
      aria-label={RAIL_LABEL}
      data-index-status={indexStatus}
      data-location-status={viewportTarget?.context === context ? 'located' : pendingOpenRef.current ? 'pending' : 'inactive'}
      data-indexed-targets={targets.length}
      data-rendered-targets={displayTargets.length}
      data-history-total={indexTotal || loadedCanonicalTotal}
      data-index-requests={indexMetrics.requests}
      data-index-duration-ms={Math.round(indexMetrics.durationMs)}
    >
      {hovered && createPortal(
        <div
          className="message-navigation-tooltip message-navigation-tooltip-floating"
          role="tooltip"
          data-preview-mode={hovered.mode}
          data-preview-from-end={hovered.target.fromEnd}
          style={{
            top: hovered.mode === 'scrub'
              ? `clamp(56px, ${hovered.top}px, calc(100dvh - 56px))`
              : hovered.top,
            left: hovered.left,
          }}
        >
          <strong>{KIND_META[hovered.target.kind].label}</strong>
          <span>{hovered.target.preview || PREVIEW_FALLBACK}</span>
        </div>,
        document.body,
      )}

      <div
        ref={listRef}
        className="message-navigation-list"
        onScroll={event=>setListScroll(event.currentTarget.scrollTop)}
        onPointerMove={moveScrub}
        onPointerUp={(event) => finishScrub(event, false)}
        onPointerCancel={(event) => finishScrub(event, true)}
        onLostPointerCapture={(event) => finishScrub(event, true)}
      >
        <div aria-hidden="true" style={{height:virtualStart*rowHeight,flex:'0 0 auto'}} />
        {displayTargets.map((target) => {
          const meta = KIND_META[target.kind];
          return (
            <div className="message-navigation-marker-wrap" style={{height:rowHeight,display:'flex',alignItems:'center'}} key={`${target.kind}-${target.offset}`}>
              <button
                type="button"
                className={`message-navigation-marker message-navigation-marker-${meta.slug}${activeOffset === target.offset ? ' is-jumped' : ''}${viewportTarget?.context === context && viewportTarget.offset === target.offset ? ' is-viewport-target' : ''}`}
                onClick={(event) => handleMarkerClick(event, target)}
                onPointerDown={(event) => beginScrub(event, target)}
                onMouseEnter={(event) => showPreview(event, target)}
                onMouseLeave={() => {
                  if (!scrubGestureRef.current?.active) setHovered(null);
                }}
                aria-label={`${meta.label}: ${target.preview || PREVIEW_FALLBACK}`}
                title={target.preview || PREVIEW_FALLBACK}
                data-from-end={target.fromEnd}
                data-canonical-offset={target.offset}
                data-message-id={target.messageId}
                aria-current={viewportTarget?.context === context && viewportTarget.offset === target.offset ? 'location' : undefined}
                data-kind={target.kind}
                disabled={jumpingOffset !== null}
              >
                {jumpingOffset === target.offset
                  ? <Loader2 size={13} className="animate-spin" />
                  : <MarkerGlyph kind={target.kind} />}
              </button>
            </div>
          );
        })}
        <div aria-hidden="true" style={{height:(targets.length-virtualEnd)*rowHeight,flex:'0 0 auto'}} />
        {targets.length === 0 && <span className="message-navigation-empty">{'\u00b7'}</span>}
      </div>

      {(indexStatus === 'loading' || indexStatus === 'error') && (
        <button type="button" data-navigation-load disabled={indexStatus === 'loading'}
          className={`message-navigation-status${indexStatus === 'loading' ? '' : ' message-navigation-status-error'}`}
          aria-label={indexStatus === 'loading' ? 'Loading message navigation' : 'Retry message navigation'}
          title={indexError ?? 'Loading complete message navigation'}
          onClick={retryIndex}>
          {indexStatus === 'loading' ? <Loader2 size={11} className="animate-spin" /> : indexStatus === 'error' ? '!' : '…'}
        </button>
      )}
      {indexError && <span className="sr-only" role="status">{indexError}</span>}
      {jumpError && <div className="message-navigation-jump-error" role="status">{jumpError}</div>}
    </aside>
  );
}
