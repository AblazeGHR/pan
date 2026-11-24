// Build the existing Zotero Agent source into an isolated fixture directory.
// Never write to its source checkout or invoke its destructive dev harness.
import { createRequire } from 'node:module';
import { cpSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
const [source, out] = process.argv.slice(2);
const require = createRequire(join(source, 'package.json'));
const { build } = require('esbuild');
mkdirSync(out, { recursive: true });
cpSync(join(source, 'dist', 'addon'), out, { recursive: true });
const common = { bundle:true, format:'iife', target:'firefox140', platform:'neutral',
  mainFields:['module','main'], legalComments:'none', charset:'utf8', minify:false,
  define:{'process.env.NODE_ENV':'"production"', __TEST_HOOKS__:'true'} };
await build({...common, entryPoints:[join(source,'src','zotero','plugin.ts')],
  outfile:join(out,'plugin.js'), globalName:'ZoteroChat'});
await build({...common, entryPoints:[join(source,'src','zotero','panel.ts')],
  outfile:join(out,'panel.js'), globalName:'ZoteroChatPanel'});
