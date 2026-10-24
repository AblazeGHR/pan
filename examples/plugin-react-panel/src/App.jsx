import React, { useState } from 'react';

export default function App() {
  const [health, setHealth] = useState('Not called yet.');
  const [lazy, setLazy] = useState('Lazy chunk not loaded.');

  async function callHealth() {
    setHealth('Calling…');
    try {
      const response = await fetch('/api/health', { headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = await response.json();
      setHealth(`ok · Pan ${body.version}`);
      document.documentElement.dataset.panCall = 'ok';
    } catch (error) {
      setHealth(`failed · ${error.message}`);
      document.documentElement.dataset.panCall = 'failed';
    }
  }

  async function loadLazy() {
    setLazy('Loading…');
    const module = await import('./lazy.jsx');
    setLazy(module.default);
    document.documentElement.dataset.lazy = module.default;
  }

  return (
    <main className="panel">
      <header>
        <h1>React Console</h1>
        <p className="muted">A real Vite + React build served from an immutable panel build URL.</p>
      </header>
      <section className="card">
        <h2>Pan API</h2>
        <button type="button" onClick={callHealth}>Call /api/health</button>
        <output id="health" role="status">{health}</output>
      </section>
      <section className="card">
        <h2>Lazy chunk</h2>
        <p className="muted">This chunk is requested only when clicked, so an open page exercises its old build URL.</p>
        <button type="button" onClick={loadLazy}>Load lazy chunk</button>
        <output id="lazy" role="status">{lazy}</output>
      </section>
    </main>
  );
}
