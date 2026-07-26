import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';

export default defineConfig(({ mode }) => {
  const isProd = mode === 'production';
  const base = isProd ? '/react/' : '/';

  return {
    root: 'src',
    plugins: [react(), tailwindcss()],
    base,
    resolve: {
      alias: {
        '@': path.resolve(__dirname, 'src'),
      },
    },
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: 'http://localhost:8767',
          changeOrigin: true,
        },
        '/ws': {
          target: 'ws://localhost:8767',
          ws: true,
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: '../dist',
      emptyOutDir: true,
      sourcemap: true,
      rollupOptions: {
        output: {
          manualChunks: {
            'react-vendor': ['react', 'react-dom', 'react-router-dom'],
            'markdown-vendor': [
              'react-markdown',
              'remark-gfm',
              'rehype-highlight',
              'rehype-katex',
            ],
            'monaco-vendor': ['@monaco-editor/react'],
          },
        },
      },
    },
  };
});
