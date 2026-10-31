import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import CanonicalJobsView from './views/CanonicalJobsView';
import { useAutomationAuth } from './stores/automationStore';
import { useWebSocket } from './hooks/useWebSocket';
import { ToastContainer } from './components/ui/Toast';
import './index.css';

useAutomationAuth.setState({ token: window.__ownedR14Token, expiresAt: Date.now() + 600000 });
function Owned() {
  useWebSocket();
  return <MemoryRouter><CanonicalJobsView /><ToastContainer /></MemoryRouter>;
}
createRoot(document.getElementById('root')!).render(<Owned />);
