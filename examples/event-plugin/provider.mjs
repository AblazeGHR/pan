// Dependency-free Node event/capability provider. stdout is JSON-RPC only.
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';

const root = process.env.PAN_PLUGIN_DATA_DIR;
if (!root) throw new Error('PAN_PLUGIN_DATA_DIR required');
fs.mkdirSync(root, {recursive: true});
const record = value => fs.appendFileSync(path.join(root, 'observed.jsonl'), JSON.stringify(value) + '\n');
const send = value => process.stdout.write(JSON.stringify({jsonrpc: '2.0', ...value}) + '\n');
let generation;
let ready = false;
let sequence = 0;
record({kind: 'started', pid: process.pid, role: process.env.PAN_PLUGIN_ROLE});
readline.createInterface({input: process.stdin}).on('line', line => {
  const message = JSON.parse(line);
  if (!message.method) {
    record({kind: 'publication', ...message});
    return;
  }
  const params = message.params;
  let result;
  switch (message.method) {
    case 'provider.initialize':
      result = {protocol_version: 'pan-capability/v1', extensions: ['pan-events/v1']};
      break;
    case 'events.initialize':
      generation = params.generation;
      record({kind: 'registered', ...params});
      result = {ready: true};
      break;
    case 'events.ready':
      ready = true;
      record({kind: 'ready', generation});
      result = {accepted: true};
      break;
    case 'events.deliver':
      if (fs.existsSync(path.join(root, 'stall'))) return;
      record({kind: 'delivery', ...params});
      result = {accepted: true};
      break;
    case 'capability.invoke':
      result = {echo: params.arguments};
      break;
    default:
      send({id: message.id, error: {code: -32601, message: 'Unknown method'}});
      return;
  }
  send({id: message.id, result});
}).on('close', () => process.exit(0));

// The demo's file is its own event producer, not a Pan publication endpoint.
// Replace with the plugin's actual socket/device/domain observation in production.
setInterval(() => {
  const inbox = path.join(root, 'publish.json');
  if (!ready || !fs.existsSync(inbox)) return;
  const params = JSON.parse(fs.readFileSync(inbox, 'utf8'));
  fs.unlinkSync(inbox);
  send({id: 'plugin-' + (++sequence), method: 'events.publish', params: {generation, ...params}});
}, 30);
