import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import { router } from './router';
import { useAppSettingsStore } from './stores/appSettingsStore';
import './index.css';

// Load persisted app settings (config.json ui) at startup — the store renders
// defaults until the GET resolves, then updates in place. Fire-and-forget.
useAppSettingsStore.getState().loadSettings();

const root = document.getElementById('root');
if (!root) throw new Error('Root element not found');

createRoot(root).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
