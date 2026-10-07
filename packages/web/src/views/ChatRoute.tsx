import { deferredView } from './deferredView';

// The known initial Chat URL keeps its early parallel download. Other URLs
// import this wrapper through the router without starting the chat graph.
const initialChat = typeof window !== 'undefined'
  && window.location.pathname.replace(/\/$/, '') === import.meta.env.BASE_URL.replace(/\/$/, '');
export default deferredView(() => import('./ChatView'), initialChat);
