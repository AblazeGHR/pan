import { lazy, Suspense } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import App from './App';
import ChatRoute from './views/ChatRoute';
import { deferredView } from './views/deferredView';

const EditorView = deferredView(() => import('./views/EditorView'));

const ManageView = lazy(() => import('./views/ManageView'));
const JobsView = lazy(() => import('./views/JobsView'));
const TerminalPanel = lazy(() => import('./views/TerminalPanel'));

function deferredRoute(view: React.ReactNode) {
  return <Suspense fallback={<div className="p-4 text-sm text-text-tertiary">Loading...</div>}>{view}</Suspense>;
}

const isProd = import.meta.env.PROD;
const basename = isProd ? '/react' : '/';

export const router = createBrowserRouter(
  [
    {
      path: '/',
      element: <App />,
      children: [
        {
          index: true,
          element: <ChatRoute />,
        },
        {
          path: 'editor',
          element: <EditorView />,
        },
        {
          path: 'manage/:sessionId',
          element: deferredRoute(<ManageView />),
        },
        {
          path: 'jobs',
          element: deferredRoute(<JobsView />),
        },
        {
          path: 'terminals',
          element: deferredRoute(<TerminalPanel />),
        },
        {
          path: 'schedules',
          element: <Navigate replace to="/jobs" />,
        },
      ],
    },
  ],
  { basename },
);
