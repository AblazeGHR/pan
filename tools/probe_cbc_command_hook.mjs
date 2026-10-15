// Run the installed command executor and generation processor only. No CBC
// bootstrap, profile, authentication, provider, or session store is loaded.
import fs from 'node:fs';
import crypto from 'node:crypto';
import {spawn} from 'node:child_process';
import {StringDecoder} from 'node:string_decoder';
import {once} from 'node:events';

const source = fs.readFileSync(process.argv[2], 'utf8');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
function between(start, end, from = 0) {
  const a = source.indexOf(start, from);
  const b = source.indexOf(end, a);
  if (a < 0 || b < 0) throw new Error('Installed command hook boundary changed');
  return source.slice(a, b);
}
const execute = between('async execute(eA,el,ec){', 'deriveHookStatus(eA){', 8880000);
const parse = between('parseHookOutput(eA,el,ec,eu,ed){', '};hook_executor_decorate', 8880000);
const processor = between('async process(eA,el){', ';generation_context_hook_input_processor_decorate', source.indexOf('let gd=class'));
const dk = {t7: async () => {}};
const dQ = {Pz: ({baseEnv, env}) => ({...baseEnv, ...env})};
const c$ = {uF: () => true, vg: () => ({executable: input.shell, argsPrefix: ['-c']}), _V: x => x};
const tryBuildDirectPowerShellHookCommand = () => undefined;
const buildHookSpawnOptions = env => ({env, windowsHide: true});
const dI = {spawn};
const dx = {u_: () => {}, X3: () => {}};
const dS = {y: StringDecoder};
const dM = 12000;
const uh = {mb: {USER_PROMPT_SUBMIT: 'UserPromptSubmit', STOP: 'Stop', SUBAGENT_STOP: 'SubagentStop'}};
const noop = () => {};
let hookPid, retirement;
const host = {
  ...eval('({' + execute + ',' + parse + '})'),
  getEnvironment: () => input.environment,
  onSpawn: child => {hookPid = child.pid; retirement = once(child, 'close');},
  logger: {info: noop, warn: noop, debug: noop, error: noop},
  debugService: {debug: noop},
};
// The extracted class ends with one class-closing brace after its method.
const generation = eval('({' + processor.slice(0, -1) + '})');
await generation.process(input.event);
const result = await host.execute(input.command, input.event, {hookEvent: input.event.hook_event_name});
const [retiredExitCode] = await retirement;
process.stdout.write(JSON.stringify({
  bundleSHA256: crypto.createHash('sha256').update(source).digest('hex'),
  exitCode: result.exitCode, allowed: result.allowed, stderr: result.stderr,
  hookPid, retiredExitCode,
}));
