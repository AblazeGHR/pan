/* Small dependency-free builder: hashed assets + relative URLs + SPA entry.
 * Copy this file as a starting point for a Vite/React/Vue `build` command. */
import { createHash } from 'node:crypto';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const source = path.join(root, 'src');
const output = path.join(root, 'dist');

const hash = (value) => createHash('sha256').update(value).digest('hex').slice(0, 10);
const [html, script, styles] = await Promise.all(
  ['index.html', 'app.js', 'styles.css'].map((name) => readFile(path.join(source, name), 'utf8')),
);

const jsName = `app-${hash(script)}.js`;
const cssName = `styles-${hash(styles)}.css`;
await rm(output, { recursive: true, force: true });
await mkdir(path.join(output, 'assets'), { recursive: true });
await writeFile(path.join(output, 'assets', jsName), script);
await writeFile(path.join(output, 'assets', cssName), styles);
await writeFile(path.join(output, 'index.html'), html
  .replace('__STYLE__', `assets/${cssName}`)
  .replace('__SCRIPT__', `assets/${jsName}`));
console.log(`built ${path.relative(root, output)} (${jsName}, ${cssName})`);
