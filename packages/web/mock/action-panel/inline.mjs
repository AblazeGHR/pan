import { readFileSync, writeFileSync, rmSync } from 'node:fs';
import path from 'node:path';

// Inline Vite's single JS/CSS output so the mock opens directly from file://.
// Usage: node inline.mjs <build-dir> [--clean]
const dir = path.resolve(process.argv[2] ?? '../../audit/action-panel-mock-20261021/preview');
const clean = process.argv.includes('--clean');
const htmlPath = path.join(dir, 'index.html');
let html = readFileSync(htmlPath, 'utf8');

function asset(url, css = false) {
  let text = readFileSync(path.join(dir, url.replace(/^\.\//, '')), 'utf8')
    .replace(/<\/script/gi, '<\/script')
    .replace(/<\/style/gi, '<\/style');
  if (css) {
    // The production stylesheet fetches Inter from Google Fonts. The mock embeds
    // the same family as a data URI, so drop the remote import for offline use.
    text = text.replace(/@import\s+url\(['"]?https?:\/\/fonts\.googleapis\.com[^)]*\)\s*;\s*/g, '');
    text = text.replace(/@import\s*["']https?:\/\/fonts\.googleapis\.com[^"']*["']\s*;\s*/g, '');
  }
  return text;
}

html = html.replace(/<script type="module" crossorigin src="([^"]+)"><\/script>/, (_, src) =>
  `<script type="module">\n${asset(src)}\n</script>`,
);
html = html.replace(/<link rel="stylesheet" crossorigin href="([^"]+)">/, (_, href) =>
  `<style>\n${asset(href, true)}\n</style>`,
);
writeFileSync(path.join(dir, 'preview.html'), html);

if (clean) {
  rmSync(path.join(dir, 'index.html'), { force: true });
  rmSync(path.join(dir, 'assets'), { recursive: true, force: true });
  rmSync(path.join(dir, 'app.js'), { force: true });
}
console.log(`wrote ${path.join(dir, 'preview.html')} (${(html.length / 1024).toFixed(0)} KiB)`);
