import { lazy, Suspense } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import App from './App';
import ChatRoute from './views/ChatRoute';
import { deferredView } from './views/deferredView';

const EditorView = deferredView(() => import('./views/EditorView'));

const ManageView = lazy(() => import('./views/ManageView'));
const TerminalPanel = lazy(() => import('./views/TerminalPanel'));
const PluginPanelView = lazy(() => import('./views/PluginPanelView'));
const LockerView = lazy(() => import('./views/LockerView'));
const ActionsView = lazy(() => import('./views/ActionsView'));
const CanonicalJobsView = lazy(() => import('./views/CanonicalJobsView'));
const AutomationView = lazy(() => import('./views/AutomationView'));

import { RetiredJobsRoute } from './views/RetiredJobsRoute';


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
        { path: 'locker', element: deferredRoute(<LockerView />) },
        { path: 'actions', element: deferredRoute(<ActionsView />) },
        { path: 'automation/canonical', element: deferredRoute(<CanonicalJobsView />) },
        { path: 'automation', element: deferredRoute(<AutomationView />) },
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
          element: <RetiredJobsRoute />,
        },
        {
          path: 'terminals',
          element: deferredRoute(<TerminalPanel />),
        },
        {
          path: 'plugins/:pluginId/:panelId',
          element: deferredRoute(<PluginPanelView />),
        },
        {
          path: 'schedules',
          element: <Navigate replace to="/automation/canonical" />,
        },
      ],
    },
  ],
  { basename },
);

