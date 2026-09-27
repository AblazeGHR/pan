import { lazy, Suspense } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import App from './App';
import ChatView from './views/ChatView';
import EditorView from './views/EditorView';

const ManageView = lazy(() => import('./views/ManageView'));
const JobsView = lazy(() => import('./views/JobsView'));

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
          element: <ChatView />,
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
          path: 'schedules',
          element: <Navigate replace to="/jobs" />,
        },
      ],
    },
  ],
  { basename },
);
