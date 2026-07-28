import { useState, useEffect } from 'react';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Sidebar } from './components/layout/Sidebar';
import { ToastContainer } from './components/ui/Toast';
import { CommandPalette } from './components/CommandPalette';
import { useMediaQuery } from './hooks/useMediaQuery';
import { useUIStore } from './stores/uiStore';
import { Outlet, useNavigate } from 'react-router-dom';

function Layout() {
  const { isMobile } = useMediaQuery();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const theme = useUIStore((s) => s.theme);
  const navigate = useNavigate();

  // Close sidebar on mobile when switching out of mobile
  useEffect(() => {
    if (!isMobile) setSidebarOpen(false);
  }, [isMobile]);

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
    <div className="flex h-screen bg-bg-primary text-text-primary overflow-hidden">
      {/* Mobile hamburger button */}
      {isMobile && (
        <button
          onClick={() => setSidebarOpen(!sidebarOpen)}
          className="fixed top-2 left-2 z-50 rounded bg-bg-tertiary border border-border-default p-1.5 text-text-primary"
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
      {isMobile && sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar — full overlay on mobile */}
      <div
        className={`${
          isMobile
            ? `fixed inset-y-0 left-0 z-40 transform transition-transform duration-200 ${
                sidebarOpen ? 'translate-x-0' : '-translate-x-full'
              }`
            : 'relative'
        }`}
      >
        <Sidebar />
      </div>

      <main className={`flex-1 flex flex-col min-w-0 overflow-hidden ${isMobile ? 'pt-10' : ''}`}>
        <Outlet />
      </main>
      <ToastContainer />
      <CommandPalette />
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
