/* eslint-disable react-refresh/only-export-components */
/* Message bookmarks — phase 1 interactive mock harness.
 *
 * ISOLATION: this page is a standalone preview.  It shares no state with the
 * production app: the bookmark store below is an in-memory mock that mirrors
 * the phase-1 backend contract (per-session envelope, revision, idempotent
 * add/remove, optional compare-and-set, bookmarks.changed events), and the
 * fixture sessions are static.  Nothing here is wired into the real app, and
 * opening it does not enable any production bookmark entry point.
 *
 * FIDELITY: chat rows, source pills, the navigation rail markers and all
 * theme tokens reuse the real index.css structure and classes.  The rows and
 * rail are mock mirrors (not the production components) because phase 2 is
 * what teaches those components about bookmarks; every mirrored element
 * carries the same class names and data attributes as production.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Bookmark, BookmarkMinus, BookmarkPlus, Check, ListTree, Send, UserRound } from 'lucide-react';
import { useMediaQuery } from './hooks/useMediaQuery';
import './index.css';
import './message-bookmarks-harness.css';

/* ------------------------------------------------------------------ types */

type NavKind = 'user' | 'worker' | 'system' | 'maAssign' | 'maMsg';
type MarkerKind = NavKind | 'bookmark';

interface MockBlock {
  messageId: string;
  role: 'user' | 'assistant' | 'thinking' | 'tool' | 'system';
  content: string;
  /** Existing message-navigation tag; null means the block has no nav tag. */
  navKind: NavKind | null;
  preview: string;
  /** The real manual report binds to a task's tail block; the mock marks one. */
  reportable?: boolean;
}

interface MockSession {
  id: string;
  name: string;
  blocks: MockBlock[];
}

interface BookmarkEntry {
  messageId: string;
  createdAt: string;
}

interface Envelope {
  revision: number;
  bookmarks: Record<string, BookmarkEntry>;
  updatedAt: string | null;
  state: 'absent' | 'present';
}

interface BookmarkEvent {
  type: 'bookmarks.changed';
  sessionId: string;
  revision: number;
  action: 'added' | 'removed';
  messageIds: string[];
}

interface MutateResult {
  ok: boolean;
  changed: boolean;
  revision: number;
  bookmarks: Record<string, BookmarkEntry>;
  error?: string;
}

/* ------------------------------------------------- phase-1 backend mirror */

const ABSENT: Envelope = { revision: 0, bookmarks: {}, updatedAt: null, state: 'absent' };
const bookmarkStore = new Map<string, Envelope>();
const eventLog: BookmarkEvent[] = [];
const listeners = new Set<() => void>();

function readEnvelope(sessionId: string): Envelope {
  return bookmarkStore.get(sessionId) ?? ABSENT;
}

function notify(): void {
  for (const listener of [...listeners]) listener();
}

/** Mirrors add/remove: idempotent, optional CAS, event only on real change. */
function mutate(sessionId: string, messageId: string, add: boolean,
                baseRevision: number | null): MutateResult {
  const session = SESSIONS.find((item) => item.id === sessionId);
  const known = session?.blocks.some((block) => block.messageId === messageId) ?? false;
  if (!known) {
    // The real API resolves the identity against the session's history; the
    // mock refuses ids that do not belong to this session's blocks.
    return { ok: false, changed: false, revision: readEnvelope(sessionId).revision,
             bookmarks: readEnvelope(sessionId).bookmarks, error: 'message_not_found' };
  }
  const current = readEnvelope(sessionId);
  if (baseRevision !== null && baseRevision !== current.revision) {
    return { ok: false, changed: false, revision: current.revision,
             bookmarks: current.bookmarks, error: 'bookmark_conflict' };
  }
  const present = messageId in current.bookmarks;
  const changed = add ? !present : present;
  if (!changed) {
    return { ok: true, changed: false, revision: current.revision, bookmarks: current.bookmarks };
  }
  const bookmarks = { ...current.bookmarks };
  if (add) {
    bookmarks[messageId] = { messageId, createdAt: new Date().toISOString() };
  } else {
    delete bookmarks[messageId];
  }
  const next: Envelope = {
    revision: current.revision + 1,
    bookmarks,
    updatedAt: new Date().toISOString(),
    state: 'present',
  };
  bookmarkStore.set(sessionId, next);
  eventLog.unshift({ type: 'bookmarks.changed', sessionId, revision: next.revision,
                     action: add ? 'added' : 'removed', messageIds: [messageId] });
  notify();
  return { ok: true, changed: true, revision: next.revision, bookmarks };
}

/* ----------------------------------------------------------------- fixtures */

const SESSION_A: MockSession = {
  id: 'ses_bookmarkdemoaaaa',
  name: 'Session A — panic 定位',
  blocks: [
    { messageId: 'pan:1111111111111111111111111111aa01', role: 'user',
      content: '帮我看下这个启动报错：thread \'main\' panicked at \'index out of bounds\'',
      navKind: 'user', preview: '帮我看下这个启动报错' },
    { messageId: 'pan:1111111111111111111111111111aa02', role: 'assistant',
      content: '我先读一下源码，定位 panic 的调用链，再看最近的改动。',
      navKind: null, preview: '我先读一下源码' },
    { messageId: 'pan:1111111111111111111111111111aa03', role: 'thinking',
      content: 'The stack points at src/main.rs:42; the slice bound is computed before the rename loop mutates the vector.',
      navKind: null, preview: 'The stack points at src/main.rs:42' },
    { messageId: 'pan:1111111111111111111111111111aa04', role: 'tool',
      content: 'Read(src/main.rs) → 42: let name = names[i];',
      navKind: null, preview: 'Read(src/main.rs)' },
    { messageId: 'pan:1111111111111111111111111111aa05', role: 'assistant',
      content: '定位完成：越界发生在批量重命名的边界条件上。当待重命名列表在迭代中变化时，索引未重新校验。补丁已附在这个 block 之后。',
      navKind: 'worker', preview: '定位完成：越界发生在批量重命名', reportable: true },
    { messageId: 'pan:1111111111111111111111111111aa06', role: 'user',
      content: 'MA assign: 复盘本次定位过程，整理成可检索的 notes',
      navKind: 'maAssign', preview: 'MA assign: 复盘本次定位过程' },
    { messageId: 'pan:1111111111111111111111111111aa07', role: 'system',
      content: 'Session snapshot restored from archive',
      navKind: 'system', preview: 'Session snapshot restored' },
    { messageId: 'pan:1111111111111111111111111111aa08', role: 'user',
      content: '@@@@by agent: 已收到，开始整理 notes',
      navKind: 'maMsg', preview: '已收到，开始整理 notes' },
  ],
};

const SESSION_B: MockSession = {
  id: 'ses_bookmarkdemobbbb',
  name: 'Session B — 隔离验证',
  blocks: [
    { messageId: 'pan:2222222222222222222222222222bb01', role: 'user',
      content: '第二个会话：确认书签不会串会话。',
      navKind: 'user', preview: '第二个会话：确认书签不会串会话' },
    { messageId: 'pan:2222222222222222222222222222bb02', role: 'assistant',
      content: '这是另一个 Session 的正文块，默认没有任何书签。',
      navKind: null, preview: '这是另一个 Session 的正文块' },
    { messageId: 'pan:2222222222222222222222222222bb03', role: 'assistant',
      content: '切回 Session A 后，A 的书签应当原样保留。',
      navKind: null, preview: '切回 Session A 后' },
  ],
};

const SESSIONS: MockSession[] = [SESSION_A, SESSION_B];

function seedSessionB(): void {
  // Session B starts with one pre-existing bookmark so the isolation demo has
  // a visible difference without any interaction.
  const seeded = SESSION_B.blocks[1];
  if (!seeded) return;
  bookmarkStore.set(SESSION_B.id, {
    revision: 1,
    bookmarks: { [seeded.messageId]:
      { messageId: seeded.messageId, createdAt: '2026-10-14T00:00:00' } },
    updatedAt: '2026-10-14T00:00:00',
    state: 'present',
  });
}

seedSessionB();

const KIND_META: Record<MarkerKind, { label: string; slug: string; cn: string; glyph?: string }> = {
  user:     { label: 'User',      slug: 'user',      cn: '用户',   glyph: undefined },
  worker:   { label: 'TA report', slug: 'worker',    cn: 'TA 汇报', glyph: 'Re' },
  system:   { label: 'System',    slug: 'system',    cn: '系统',   glyph: 'Sys' },
  maAssign: { label: 'MA assign', slug: 'ma-assign', cn: 'MA 派发', glyph: 'MA' },
  maMsg:    { label: 'MA msg',    slug: 'ma-msg',    cn: 'MA 消息', glyph: 'MA' },
  bookmark: { label: 'Bookmark',  slug: 'bookmark',  cn: '书签',   glyph: undefined },
};

const SOURCE_TAG_LABELS: Record<string, string> = {
  'ta-report': 'TA report',
  system: 'System',
  'ma-assign': 'MA assign',
};

/* ------------------------------------------------------------------- parts */

function MarkerGlyph({ kind }: { kind: MarkerKind }) {
  if (kind === 'bookmark') return <Bookmark size={13} strokeWidth={2.4} />;
  if (kind === 'user') return <UserRound size={13} strokeWidth={2.4} />;
  return <span className="message-navigation-marker-tag">{KIND_META[kind].glyph}</span>;
}

/** A bookmark-decorated existing tag composites the bookmark over its own
    glyph: original icon + bottom-right bookmark badge, unified color.  A new
    bookmark tag (no original glyph) is just the bookmark icon. */
function MarkerIcon({ kind, bookmarked }: { kind: MarkerKind; bookmarked: boolean }) {
  if (kind === 'bookmark') return <Bookmark size={13} strokeWidth={2.4} />;
  if (!bookmarked) return <MarkerGlyph kind={kind} />;
  return (
    <span className="bookmark-icon-composite" data-composite="true">
      <MarkerGlyph kind={kind} />
      <Bookmark className="bookmark-icon-badge" size={9} strokeWidth={2.8} />
    </span>
  );
}

interface Marker {
  key: string;
  messageId: string;
  kind: MarkerKind;
  /** Label as it appears in the rail tooltip/aria (English UI convention). */
  label: string;
  preview: string;
  bookmarked: boolean;
  /** True when the bookmark created this tag (block had no nav tag). */
  isNew: boolean;
}

function buildMarkers(session: MockSession, envelope: Envelope): Marker[] {
  const markers: Marker[] = [];
  for (const block of session.blocks) {
    const bookmarked = block.messageId in envelope.bookmarks;
    if (block.navKind) {
      markers.push({
        key: `${block.navKind}-${block.messageId}`,
        messageId: block.messageId,
        kind: block.navKind,
        label: bookmarked ? `${KIND_META[block.navKind].label} (Bookmark)` : KIND_META[block.navKind].label,
        preview: block.preview,
        bookmarked,
        isNew: false,
      });
    } else if (bookmarked) {
      // A block without a navigation tag gets a new bookmark tag, positioned
      // in the original message order.
      markers.push({
        key: `bookmark-${block.messageId}`,
        messageId: block.messageId,
        kind: 'bookmark',
        label: KIND_META.bookmark.label,
        preview: block.preview,
        bookmarked: true,
        isNew: true,
      });
    }
  }
  return markers;
}

function BlockRow({ block, bookmarked, bookmarkable, menuOpen, reported, onOpenMenu }:
  { block: MockBlock; bookmarked: boolean; bookmarkable: boolean;
    menuOpen: boolean; reported: boolean;
    onOpenMenu: (block: MockBlock, point: { clientX: number; clientY: number }) => void }) {
  const sourceTag = block.navKind === 'worker' ? 'ta-report'
    : block.navKind === 'system' ? 'system'
    : block.navKind === 'maAssign' ? 'ma-assign' : null;
  const rowClass = block.role === 'user' ? 'message-row message-row-user'
    : block.role === 'system' ? 'system-message'
    : 'message-row message-row-assistant';
  // The hit area is the row's left gutter: from the leftmost edge to the
  // block's colored vertical line.  Its width is the row's left padding,
  // measured so responsive breakpoints stay exact.
  const rowRef = useRef<HTMLDivElement | null>(null);
  const [gutterWidth, setGutterWidth] = useState(0);
  useLayoutEffect(() => {
    const row = rowRef.current;
    if (!row) return;
    const measure = () => setGutterWidth(parseFloat(getComputedStyle(row).paddingLeft) || 0);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(row);
    window.addEventListener('resize', measure);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, []);
  return (
    <div
      ref={rowRef}
      className={`${rowClass} ${sourceTag ? 'message-row-worker-report' : ''} bookmark-row px-3 sm:px-6 lg:px-8`}
      data-message-id={block.messageId}
      data-bookmark-state={bookmarked ? 'on' : 'off'}
      data-nav-kind={block.navKind ?? 'none'}
    >
      {bookmarkable ? (
        <button
          type="button"
          className="bookmark-rail-strip"
          data-bookmark-strip={block.messageId}
          style={{ width: gutterWidth || undefined }}
          aria-label={`Bookmark menu: ${block.preview}`}
          aria-expanded={menuOpen}
          onClick={(event) => onOpenMenu(block, { clientX: event.clientX, clientY: event.clientY })}
        />
      ) : null}
      {sourceTag ? (
        <span className="worker-report-label" data-source-tag={sourceTag}>
          {SOURCE_TAG_LABELS[sourceTag]}
        </span>
      ) : null}
      {block.role === 'system' ? (
        <span className="msg system text-xs text-text-tertiary bg-bg-tertiary rounded px-3 py-1">
          {block.content}
        </span>
      ) : block.role === 'user' ? (
        <div className="msg user text-sm">{block.content}</div>
      ) : block.role === 'thinking' ? (
        <div className="thinking text-sm">{block.content}</div>
      ) : block.role === 'tool' ? (
        <div className="tool-group text-sm">{block.content}</div>
      ) : (
        <div className="msg assistant text-sm leading-relaxed">{block.content}</div>
      )}
      {/* Every bookmarked block carries the body bookmark tag below it; the
          rail keeps its own (decorated or new) tag without duplication. */}
      {bookmarked ? (
        <span className="bookmark-tag" data-bookmark-tag="body">
          <Bookmark size={11} /> Bookmark
        </span>
      ) : null}
      {block.reportable && reported ? (
        <span className="bookmark-reported" data-manual-reported="true">
          <Check size={11} /> 已汇报
        </span>
      ) : null}
    </div>
  );
}

/* ----------------------------------------------------------------- harness */

declare global {
  interface Window {
    __bookmarkMock: {
      envelope: (sessionId: string) => Envelope;
      events: () => BookmarkEvent[];
      sessions: () => { id: string; messageIds: string[] }[];
      reset: () => void;
    };
  }
}

function Harness() {
  const autoMobile = useMediaQuery();
  const [sessionId, setSessionId] = useState(SESSION_A.id);
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');
  const [layout, setLayout] = useState<'auto' | 'desktop' | 'mobile'>('auto');
  const [railOpen, setRailOpen] = useState(false);
  const [menuBlockId, setMenuBlockId] = useState<string | null>(null);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);
  // Mirrors the production navigation-tag click rule: the clicked tag becomes
  // the active tag with the exact nearest-tag highlight style
  // (is-viewport-target), and real user input releases it so it is never
  // locked; a session switch never carries it over.
  const [activeMarkerId, setActiveMarkerId] = useState<string | null>(null);
  const activeFromJumpRef = useRef(false);
  const [reportedIds, setReportedIds] = useState<ReadonlySet<string>>(() => new Set());
  const [, setTick] = useState(0);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const chatRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const listener = () => setTick((value) => value + 1);
    listeners.add(listener);
    window.__bookmarkMock = {
      envelope: (id) => readEnvelope(id),
      events: () => eventLog,
      sessions: () => SESSIONS.map((session) => ({
        id: session.id, messageIds: session.blocks.map((block) => block.messageId),
      })),
      reset: () => {
        bookmarkStore.clear();
        eventLog.length = 0;
        seedSessionB();
        notify();
      },
    };
    return () => { listeners.delete(listener); };
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const session = SESSIONS.find((item) => item.id === sessionId) ?? SESSION_A;
  const envelope = readEnvelope(sessionId);
  const markers = useMemo(() => buildMarkers(session, envelope), [session, envelope]);
  const isMobile = layout === 'mobile' || (layout === 'auto' && autoMobile.isMobile);

  const openMenu = useCallback((block: MockBlock, point: { clientX: number; clientY: number }) => {
    // The hit area is the whole content block, so the menu anchors at the
    // click point instead of a fixed left-edge strip position.
    setMenuBlockId(block.messageId);
    setMenuPos({
      top: Math.min(point.clientY + 8, Math.max(8, window.innerHeight - 130)),
      left: Math.min(point.clientX + 8, Math.max(8, window.innerWidth - 180)),
    });
  }, []);

  useEffect(() => {
    if (!menuBlockId) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenuBlockId(null); };
    const onPointerDown = (event: PointerEvent) => {
      // Clicks inside the menu must reach the item; only outside presses
      // close it (capture phase, so the check has to happen here).
      const target = event.target;
      if (target instanceof Element && target.closest('[data-bookmark-menu]')) return;
      setMenuBlockId(null);
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('pointerdown', onPointerDown, true);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('pointerdown', onPointerDown, true);
    };
  }, [menuBlockId]);

  useEffect(() => {
    // Real user input releases a jump-set tag highlight (production rule: the
    // clicked tag stays active only until the reader takes over).
    const release = () => {
      if (!activeFromJumpRef.current) return;
      activeFromJumpRef.current = false;
      setActiveMarkerId(null);
    };
    window.addEventListener('pointerdown', release, true);
    window.addEventListener('wheel', release, true);
    window.addEventListener('keydown', release, true);
    return () => {
      window.removeEventListener('pointerdown', release, true);
      window.removeEventListener('wheel', release, true);
      window.removeEventListener('keydown', release, true);
    };
  }, []);

  const jumpToMarker = useCallback((messageId: string) => {
    // Mock jump: scroll the mocked chat to the block, then mark the clicked
    // tag active with the same is-viewport-target style production reuses.
    chatRef.current
      ?.querySelector(`[data-message-id="${messageId}"]`)
      ?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    activeFromJumpRef.current = true;
    setActiveMarkerId(messageId);
  }, []);

  const menuBlock = menuBlockId
    ? session.blocks.find((block) => block.messageId === menuBlockId) ?? null
    : null;
  const menuBookmarked = menuBlock ? menuBlock.messageId in envelope.bookmarks : false;

  const runAction = (add: boolean) => {
    if (!menuBlock) return;
    mutate(sessionId, menuBlock.messageId, add, null);
    setMenuBlockId(null);
  };

  return (
    <div className="bookmark-harness">
      <header className="bookmark-harness-header">
        <h1>Message bookmarks — phase 1 mock（隔离预览，非生产入口）</h1>
        <div className="bookmark-harness-group" role="group" aria-label="Session">
          <span>Session</span>
          {SESSIONS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`bookmark-harness-btn ${item.id === sessionId ? 'is-active' : ''}`}
              data-harness-session={item.id}
              onClick={() => {
                setSessionId(item.id); setRailOpen(false); setMenuBlockId(null);
                // Session identity: a tag highlight never carries across.
                activeFromJumpRef.current = false; setActiveMarkerId(null);
              }}
            >
              {item.name}
            </button>
          ))}
        </div>
        <div className="bookmark-harness-group" role="group" aria-label="Theme">
          <span>Theme</span>
          <button type="button" className={`bookmark-harness-btn ${theme === 'dark' ? 'is-active' : ''}`}
            data-harness-theme="dark" onClick={() => setTheme('dark')}>Dark</button>
          <button type="button" className={`bookmark-harness-btn ${theme === 'light' ? 'is-active' : ''}`}
            data-harness-theme="light" onClick={() => setTheme('light')}>Light</button>
        </div>
        <div className="bookmark-harness-group" role="group" aria-label="Layout">
          <span>Layout</span>
          {(['auto', 'desktop', 'mobile'] as const).map((mode) => (
            <button key={mode} type="button"
              className={`bookmark-harness-btn ${layout === mode ? 'is-active' : ''}`}
              data-harness-layout={mode}
              onClick={() => setLayout(mode)}>
              {mode}
            </button>
          ))}
        </div>
        <button type="button" className="bookmark-harness-btn" data-harness-reset
          onClick={() => { window.__bookmarkMock.reset(); setMenuBlockId(null); }}>
          Reset mock state
        </button>
      </header>

      <div className="bookmark-stage" data-layout={isMobile ? 'mobile' : 'desktop'} ref={stageRef}>
        <div className="bookmark-chat" data-testid="bookmark-chat" ref={chatRef}>
          {session.blocks.map((block) => (
            <BlockRow
              key={block.messageId}
              block={block}
              bookmarked={block.messageId in envelope.bookmarks}
              bookmarkable={block.role === 'user' || block.role === 'assistant'}
              menuOpen={menuBlockId === block.messageId}
              reported={reportedIds.has(block.messageId)}
              onOpenMenu={openMenu}
            />
          ))}
        </div>

        <button
          type="button"
          className="bookmark-mobile-toggle"
          data-bookmark-rail-toggle
          aria-expanded={railOpen}
          onClick={() => setRailOpen((open) => !open)}
        >
          <ListTree size={14} /> Navigation
        </button>

        <aside className={`bookmark-rail-column ${railOpen ? 'is-open' : ''}`} data-testid="bookmark-rail">
          <section className="bookmark-rail-panel">
            <h2>消息导航栏（镜像生产 DOM/类名）</h2>
            <div className="message-navigation-rail" aria-label="快速定位" data-bookmark-rail>
              <div className="message-navigation-list">
                {markers.map((marker) => (
                  <div className="message-navigation-marker-wrap" key={marker.key}
                    style={{ height: 27, display: 'flex', alignItems: 'center' }}>
                    <button
                      type="button"
                      className={`message-navigation-marker message-navigation-marker-${KIND_META[marker.kind].slug}${marker.bookmarked ? ' is-bookmarked' : ''}${activeMarkerId === marker.messageId ? ' is-viewport-target' : ''}`}
                      data-rail-marker={marker.messageId}
                      data-rail-kind={marker.kind}
                      data-rail-bookmarked={marker.bookmarked ? 'true' : 'false'}
                      aria-label={`${marker.label}: ${marker.preview}`}
                      title={`${marker.label}: ${marker.preview}`}
                      onClick={() => jumpToMarker(marker.messageId)}
                    >
                      <MarkerIcon kind={marker.kind} bookmarked={marker.bookmarked} />
                    </button>
                  </div>
                ))}
                {markers.length === 0 ? <span className="message-navigation-empty">·</span> : null}
              </div>
            </div>
            <p className="bookmark-rail-note" data-jump-highlight-note>
              点击任一 tag 即跳转到对应块，并被点击的 tag 会使用与「最近 tag」相同的
              反色 + 放大高亮（is-viewport-target）；滚动或点击任意处即释放，不锁死。
            </p>
            <ul className="bookmark-rail-labels" data-testid="rail-labels">
              {markers.map((marker) => (
                <li key={marker.key} data-rail-label={marker.label} data-bookmarked={marker.bookmarked ? 'true' : 'false'}>
                  <MarkerIcon kind={marker.kind} bookmarked={marker.bookmarked} />
                  <span>{marker.label}</span>
                  {marker.bookmarked && marker.kind === 'user'
                    ? <span className="bookmark-rail-note">（对应用户（书签））</span>
                    : null}
                  {marker.isNew ? <span className="bookmark-rail-note">（新增 tag）</span> : null}
                </li>
              ))}
            </ul>
          </section>

          <section className="bookmark-readout">
            <h2>Mock API 投影（bookmarks.changed 事件）</h2>
            <ul data-testid="event-log">
              <li>revision {envelope.revision} · {Object.keys(envelope.bookmarks).length} bookmarked</li>
              {eventLog.slice(0, 6).map((event, index) => (
                <li key={`${event.revision}-${index}`}>
                  {event.action} {event.messageIds[0]?.slice(0, 18)}… rev {event.revision}
                </li>
              ))}
            </ul>
          </section>

          <section className="bookmark-notes">
            <h2>最终接入方式（phase 2，待 mock 确认）</h2>
            <ol>
              <li>后端已就绪：<code>GET/PUT/DELETE /api/sessions/:id/bookmarks[/:message_id]</code>，
                身份仅接受可在该 Session history 中解析的 <code>pan:</code> messageId；
                history/navigation 响应行带 <code>bookmarked</code> 投影；变更广播 <code>bookmarks.changed</code>。</li>
              <li>可加书签范围：用户消息与 assistant 正文块；tool / thinking
                块不加书签（后端同样拒绝，错误码
                <code>message_not_bookmarkable</code>）。</li>
              <li>交互区域：每行最左侧到块的有色竖线之间的整条边距带（图中黄色区域），
                点击弹出 Add/Remove bookmark 菜单；蓝色触发显示只在这条带里，正文不加蓝条。</li>
              <li>书签标识：所有书签块在下方显示 Bookmark tag；导航栏已有 tag 改用
                合成图标（原图标 + 右下角书签角标，颜色沿用原 tag）并加
                “<code>&lt;Type&gt; (Bookmark)</code>”后缀，不新增重复 tag；无 tag 的块
                新增书签 marker，按消息顺序插入。</li>
              <li>手动汇报：原正文 meta 行的手动汇报按钮收纳进可汇报块的同一 menu，
                与书签操作并列（mock 为演示态；phase 2 与生产菜单同步落地）。</li>
              <li>流式中（未收敛、无持久 messageId）的块不可添加书签；删除或 reimport
                后身份失效的书签保留在侧车文件中、不出现在投影里，并在 GET 的
                <code>dangling</code> 中列出。</li>
            </ol>
          </section>
        </aside>
      </div>

      {menuBlock && menuPos ? (
        <div
          className="bookmark-menu"
          data-bookmark-menu
          role="menu"
          style={{ top: menuPos.top, left: menuPos.left }}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <button
            type="button"
            role="menuitem"
            data-bookmark-action={menuBookmarked ? 'remove' : 'add'}
            onClick={() => runAction(!menuBookmarked)}
          >
            {menuBookmarked ? <BookmarkMinus size={12} /> : <BookmarkPlus size={12} />}
            {menuBookmarked ? 'Remove bookmark' : 'Add bookmark'}
          </button>
          {/* The manual report button moves out of the message meta row into
              this menu for blocks that can be reported (mock demo state; the
              real reporting happens in phase 2 with the production menu). */}
          {menuBlock.reportable ? (
            <>
              <div className="bookmark-menu-divider" role="separator" />
              <button
                type="button"
                role="menuitem"
                data-manual-report={menuBlock.messageId}
                disabled={reportedIds.has(menuBlock.messageId)}
                onClick={() => {
                  setReportedIds((previous) => new Set(previous).add(menuBlock.messageId));
                  setMenuBlockId(null);
                }}
              >
                {reportedIds.has(menuBlock.messageId) ? <Check size={12} /> : <Send size={12} />}
                {reportedIds.has(menuBlock.messageId) ? '已汇报' : '手动汇报'}
              </button>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<Harness />);
