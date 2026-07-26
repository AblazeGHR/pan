import { createBrowserRouter } from 'react-router-dom';
import App from './App';
import ChatView from './views/ChatView';
import EditorView from './views/EditorView';

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
      ],
    },
  ],
  { basename },
);
