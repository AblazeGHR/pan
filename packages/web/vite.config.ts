import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';
import { brotliCompressSync, constants, gzipSync } from 'node:zlib';

function precompressAssets(): Plugin {
  return {
    name: 'precompress-static-assets',
    apply: 'build',
    generateBundle(_options, bundle) {
      for (const [fileName, output] of Object.entries(bundle)) {
        if (!/\.(?:js|css)$/.test(fileName)) continue;
        const source = output.type === 'chunk' ? output.code : output.source;
        const bytes = Buffer.from(source);
        if (bytes.length < 1024) continue;
        this.emitFile({
          type: 'asset',
          fileName: `${fileName}.br`,
          source: brotliCompressSync(bytes, {
            params: { [constants.BROTLI_PARAM_QUALITY]: 7 },
          }),
        });
        this.emitFile({
          type: 'asset',
          fileName: `${fileName}.gz`,
          source: gzipSync(bytes, { level: 6 }),
        });
      }
    },
  };
}

export default defineConfig(({ mode }) => {
  const isProd = mode === 'production';
  const base = isProd ? '/react/' : '/';

  return {
    root: 'src',
    plugins: [react(), tailwindcss(), precompressAssets()],
    base,
    resolve: {
      alias: {
        '@': path.resolve(__dirname, 'src'),
      },
    },
    test: {
      setupFiles: [path.resolve(__dirname, 'src/test-setup.ts')],
    },
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: 'http://localhost:8768',
          changeOrigin: true,
        },
        '/ws': {
          target: 'ws://localhost:8768',
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
