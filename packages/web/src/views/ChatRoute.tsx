import { deferredView } from './deferredView';
import { useSessionDeepLinkParam } from '@/utils/session-deeplink';

// The known initial Chat URL keeps its early parallel download. Other URLs
// import this wrapper through the router without starting the chat graph.
const initialChat = typeof window !== 'undefined'
  && window.location.pathname.replace(/\/$/, '') === import.meta.env.BASE_URL.replace(/\/$/, '');
const ChatView = deferredView(() => import('./ChatView'), initialChat);

export default function ChatRoute() {
  // Cold start: `/?session=<id>` opens the addressed session (notification
  // deep links / bookmarks); missing sessions degrade with a toast.
  useSessionDeepLinkParam();
  return <ChatView />;
}
