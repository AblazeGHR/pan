import { createBrowserRouter, Navigate } from 'react-router-dom';
import App from './App';
import ChatView from './views/ChatView';
import EditorView from './views/EditorView';
import ManageView from './views/ManageView';
import JobsView from './views/JobsView';

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
          element: <ManageView />,
        },
        {
          path: 'jobs',
          element: <JobsView />,
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
