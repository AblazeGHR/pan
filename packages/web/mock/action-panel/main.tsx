import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './mock.css';
import { ActionPanelMockApp } from './app';

const root = document.getElementById('root');
if (!root) throw new Error('Mock root element is missing');

createRoot(root).render(
  <StrictMode>
    <ActionPanelMockApp />
  </StrictMode>,
);
