import path from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

/**
 * Standalone build for the Action Panel redesign mock.
 * It must not participate in the production React bundle.
 */
export default defineConfig({
  root: path.resolve(__dirname, 'mock/action-panel'),
  base: './',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  build: {
    outDir: path.resolve(__dirname, '../../audit/action-panel-mock-20261021/preview'),
    emptyOutDir: true,
    assetsInlineLimit: 1024 * 1024,
    cssCodeSplit: false,
    sourcemap: false,
    rollupOptions: {
      output: {
        entryFileNames: 'app.js',
        assetFileNames: 'assets/[name][extname]',
      },
    },
  },
});
