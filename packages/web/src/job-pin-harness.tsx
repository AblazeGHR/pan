import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import JobsView from './views/JobsView';
import './index.css';
createRoot(document.getElementById('root')!).render(<MemoryRouter><div className="flex h-[100dvh] flex-col"><JobsView /></div></MemoryRouter>);
