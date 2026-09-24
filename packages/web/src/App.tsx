import { useState, useEffect, useRef } from 'react';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Sidebar } from './components/layout/Sidebar';
import { WorkspaceRail } from './components/layout/WorkspaceRail';
import { ToastContainer } from './components/ui/Toast';
import { CommandPalette } from './components/CommandPalette';
import { EditorConfirmationModal } from './components/editor/EditorConfirmationModal';
import { DetailPanel } from './components/detail/DetailPanel';
import { CliStatusBanner } from './components/layout/CliStatusBanner';
import { DemoBadge } from './demo/DemoBadge';
import { isMockMode } from './demo/mockBackend';
import { useMediaQuery } from './hooks/useMediaQuery';
import { useUIStore } from './stores/uiStore';
import { useWebSocket } from './hooks/useWebSocket';
import { Outlet, useNavigate } from 'react-router-dom';

export function Layout() {
  // The dashboard connection is route-independent. Keeping this singleton
  // consumer above <Outlet> lets editor/manage continue receiving Session,
  // worker, queue and reconnect events while ChatView is unmounted.
  useWebSocket();
  const { isMobile } = useMediaQuery();
  const mobileSidebarOpen = useUIStore((s) => s.mobileSidebarOpen);
  const setMobileSidebarOpen = useUIStore((s) => s.setMobileSidebarOpen);
  const theme = useUIStore((s) => s.theme);
  const navigate = useNavigate();
  const [mobileRailExpanded, setMobileRailExpanded] = useState(false);
  const mobileDrawerOpenedForDrop = useRef(false);

  // Track the real CSS-pixel viewport height so the app fills exactly the
  // visible area. `window.innerHeight` alone can report the layout viewport a
  // few px TALLER than the actual painted area at fractional zoom (90%/125%) —
  // the bottom (send box / last message) then falls below the viewport. Taking
  // the min with `visualViewport.height` (the truly-visible height) keeps the
  // root never larger than what's actually on screen.
  const [viewportH, setViewportH] = useState<number | undefined>(() =>
    typeof window !== 'undefined'
      ? Math.min(
          window.innerHeight,
          window.visualViewport?.height ?? window.innerHeight,
        )
      : undefined,
  );
  useEffect(() => {
    const update = () => {
      setViewportH(
        Math.min(
          window.innerHeight,
          window.visualViewport?.height ?? window.innerHeight,
        ),
      );
    };
    update();
    window.addEventListener('resize', update);
    const vv = window.visualViewport;
    if (vv) vv.addEventListener('resize', update);
    return () => {
      window.removeEventListener('resize', update);
      vv?.removeEventListener('resize', update);
    };
  }, []);

  // Close sidebar on mobile when switching out of mobile
  useEffect(() => {
    if (!isMobile) setMobileSidebarOpen(false);
  }, [isMobile, setMobileSidebarOpen]);

  // Session cards can be dragged from the mobile chat list to the screen's
  // left edge. Open the Sidebar and its attached WorkspaceRail for the drop;
  // close them again after the drop only when this drag opened the drawer.
  useEffect(() => {
    const openForDrop = () => {
      if (!useUIStore.getState().mobileSidebarOpen) {
        mobileDrawerOpenedForDrop.current = true;
        setMobileSidebarOpen(true);
      }
      setMobileRailExpanded(true);
    };
    const closeAfterDrop = () => {
      setMobileRailExpanded(false);
      if (mobileDrawerOpenedForDrop.current) {
        mobileDrawerOpenedForDrop.current = false;
        setMobileSidebarOpen(false);
      }
    };
    window.addEventListener('pan:workspace-rail-open-for-session-drop', openForDrop);
    window.addEventListener('pan:workspace-rail-close-after-session-drop', closeAfterDrop);
    return () => {
      window.removeEventListener('pan:workspace-rail-open-for-session-drop', openForDrop);
      window.removeEventListener('pan:workspace-rail-close-after-session-drop', closeAfterDrop);
    };
  }, [setMobileSidebarOpen]);

  useEffect(() => {
    if (!isMobile || !mobileSidebarOpen) {
      setMobileRailExpanded(false);
      mobileDrawerOpenedForDrop.current = false;
    }
  }, [isMobile, mobileSidebarOpen]);

  // Sync data-theme to <html>
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // Global keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey) {
        if (e.key === 'b') {
          e.preventDefault();
          useUIStore.getState().toggleSidebar();
        } else if (e.key === '1') {
          e.preventDefault();
          navigate('/');
        } else if (e.key === '2') {
          e.preventDefault();
          navigate('/editor');
        }
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [navigate]);

  return (
    <div
      className="h-screen supports-[height:100dvh]:h-dvh bg-bg-primary text-text-primary overflow-hidden"
      style={{
        height: viewportH,
        display: 'grid',
        gridTemplateColumns: 'auto 0 minmax(0,1fr) 0 auto',
        gridTemplateRows: 'minmax(0, 1fr)',
      }}
    >
      {/* Mobile hamburger button — hidden while the sidebar is open so it
          doesn't float above the overlay/sidebar and block the view. */}
      {isMobile && !mobileSidebarOpen && (
        <button
          onClick={() => setMobileSidebarOpen(!mobileSidebarOpen)}
          className="fixed top-[calc(env(safe-area-inset-top)+0.5rem)] left-2 z-50 rounded bg-bg-tertiary border border-border-default p-1.5 text-text-primary"
          aria-label="打开侧边栏"
          title="Toggle sidebar"
        >
          ☰
        </button>
      )}

      {/* Mobile overlay — sibling of sidebar, BEHIND it (z-30 < z-40).
          Previously this was nested INSIDE the sidebar container, which
          placed it above the <aside> (z-auto) within the z-40 stacking
          context — the gray backdrop covered the session list and blocked
          taps. */}
      {isMobile && mobileSidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50"
          onClick={() => setMobileSidebarOpen(false)}
        />
      )}

      {/* Sidebar drawer on mobile, Sidebar plus WorkspaceRail in the desktop
          layout. The desktop rail takes real width when expanded. */}
      <div
        className={`${
          isMobile
            ? `fixed inset-y-0 left-0 z-40 flex transform transition-[transform,width] duration-200 ${
                mobileSidebarOpen ? 'translate-x-0' : '-translate-x-full'
              }`
            : 'relative z-30 flex'
        }`}
        style={{
          gridColumn: '1',
          ...(isMobile && mobileRailExpanded ? { width: '100vw' } : {}),
        }}
        data-testid={isMobile ? 'mobile-sidebar-workspace-drawer' : undefined}
        aria-hidden={isMobile && !mobileSidebarOpen ? true : undefined}
      >
        <Sidebar mobileWorkspaceExpanded={isMobile && mobileRailExpanded} />
        {isMobile ? (
          mobileSidebarOpen && (
            <WorkspaceRail
              mobileDrawer
              mobileExpanded={mobileRailExpanded}
              onMobileExpandedChange={setMobileRailExpanded}
            />
          )
        ) : (
          <WorkspaceRail />
        )}
      </div>

      {/* Resize handle gutter — grid column 2 (0-width) */}

      {/* Main content — grid column 3 */}
      <main
        className={`flex-1 flex flex-col min-w-0 min-h-0 overflow-hidden ${isMobile ? 'pt-[calc(env(safe-area-inset-top)+0.5rem)]' : ''}`}
        style={{ gridColumn: '3' }}
      >
        <CliStatusBanner />
        <Outlet />
      </main>

      {/* Resize handle gutter — grid column 4 (0-width) */}

      <ToastContainer />
      {isMockMode() && <DemoBadge />}
      <DetailPanel />
      <CommandPalette />
      <EditorConfirmationModal />
    </div>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <Layout />
    </ErrorBoundary>
  );
}
