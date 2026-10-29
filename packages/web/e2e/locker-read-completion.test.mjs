import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ReadCompletion} from './locker-read-completion.mjs';

const req = id => ({method: () => 'PUT', url: () => `http://127.0.0.1/api/user-locker-messages/${id}/read`});
async function settle(tracker, request, status, body) {
  tracker.response({request: () => request, status: () => status, json: async () => body});
  tracker.finished(request);
  await new Promise(resolve => setImmediate(resolve));
}

test('every original identity requires a successful finished response and matching read body', async () => {
  const t = new ReadCompletion(['a', 'b']), a = req('a'), b = req('b');
  t.request(a); t.request(b);
  t.response({request: () => a, status: () => 200, json: async () => ({message: {id: 'a', read: true}})});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(t.summary().completed, 0); // headers/body do not prove request completion
  t.finished(a);
  assert.equal(t.summary().completed, 1);
  await settle(t, b, 200, {message: {id: 'b', read: true}});
  assert.deepEqual(t.summary(), {completed: 2, expected: 2, failures: []});
});
test('rejects duplicates, foreign IDs, bad status, invalid body and failed transport', async () => {
  const t = new ReadCompletion(['a', 'b']), a = req('a'), duplicate = req('a'), foreign = req('x'), b = req('b');
  for (const r of [a, duplicate, foreign, b]) t.request(r);
  await settle(t, a, 500, {message: {id: 'a', read: true}});
  await settle(t, duplicate, 200, {message: {id: 'wrong', read: true}});
  t.failed(b);
  const errors = t.summary().failures.join('|');
  for (const term of ['duplicate:a', 'unexpected:x', 'status:a:500', 'body:a', 'transport:b']) assert.ok(errors.includes(term));
});
