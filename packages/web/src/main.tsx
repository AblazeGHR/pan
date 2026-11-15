import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import { router } from './router';
import { isMockMode, installMockBackend } from './demo/mockBackend';
import './index.css';
import { LoginBoundary } from './components/LoginBoundary';
import { JobMaintenanceGate } from './components/jobs/JobMaintenanceGate';

// Mock/no-backend demo (?mock=1): intercept /api/* before anything fetches.
if (isMockMode()) installMockBackend();

const root = document.getElementById('root');
if (!root) throw new Error('Root element not found');

createRoot(root).render(
  <StrictMode>
    <LoginBoundary><JobMaintenanceGate><RouterProvider router={router} /></JobMaintenanceGate></LoginBoundary>
  </StrictMode>,
);
