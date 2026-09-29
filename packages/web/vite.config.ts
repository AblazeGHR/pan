import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';
import { readFileSync, writeFileSync } from 'node:fs';
import { brotliCompressSync, constants, gzipSync } from 'node:zlib';

function precompressAssets(): Plugin {
  return {
    name: 'precompress-static-assets',
    apply: 'build',
    writeBundle(options, bundle) {
      if (!options.dir) return;
      for (const fileName of Object.keys(bundle)) {
        if (!/\.(?:js|css)$/.test(fileName)) continue;
        // Vite replaces its preload placeholders after generateBundle. Read
        // the final written file so compressed variants are byte identical.
        const outputPath = path.join(options.dir, fileName);
        const bytes = readFileSync(outputPath);
        if (bytes.length < 1024) continue;
        writeFileSync(
          `${outputPath}.br`,
          brotliCompressSync(bytes, {
            params: { [constants.BROTLI_PARAM_QUALITY]: 7 },
          }),
        );
        writeFileSync(`${outputPath}.gz`, gzipSync(bytes, { level: 6 }));
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
          // The previous entry URL may be cached with a bad Brotli variant.
          entryFileNames: 'assets/[name]-[hash]-pc2.js',
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
