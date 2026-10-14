// Execute only the installed CLI's prompt-hook method with deterministic
// managers. No CLI bootstrap, credentials, network or model is invoked.
import fs from 'node:fs';
import crypto from 'node:crypto';

const source = fs.readFileSync(process.argv[2], 'utf8');
const start = source.indexOf('async executeUserPromptSubmitHooks(eA,el){');
const end = source.indexOf('async executeSessionStartHooks(', start);
if (start < 0 || end < 0) throw new Error('Installed hook method boundary changed');
const method = source.slice(start, end);
const MessageContentUtils = {
  hasVisibleContent: () => true,
  extractTextContent: item => item.content,
  hasAttachmentContent: () => false,
};
const InputItemUtils = {isInternalContinuationItem: () => false};
const tG = {mb: {USER_PROMPT_SUBMIT: 'UserPromptSubmit'}};
const resolveHookSessionId = session => session.id;
const buildAgentTrigger = () => ({});
const prototype = eval('({' + method + '})');
const facts = [];
const host = {
  ...prototype,
  logger: {info() {}, warn() {}, error() {}},
  getHookTranscriptPath: () => 'fixture-only',
  hookHost: {getWorkDir: () => 'fixture-only'},
  hookManager: {
    hasHooks: async () => true,
    executeHooks: async (_, event) => {
      const {session, ...fact} = event;
      facts.push(fact);
      return {allowed: true};
    },
  },
};
const inputs = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const text of inputs) {
  await host.executeUserPromptSubmitHooks({id: 'dedicated'}, [{type: 'message', role: 'user', content: text}]);
}
process.stdout.write(JSON.stringify({bundleSHA256: crypto.createHash('sha256').update(source).digest('hex'), inputs, facts}));
