// Test-only accounting for the actual per-item Read all requests.
export class ReadCompletion {
  constructor(ids) {
    this.expected = new Set(ids);
    if (this.expected.size !== ids.length) throw Error('fixture IDs must be unique');
    this.rows = [];
    this.byRequest = new WeakMap();
  }
  request(request) {
    const match = new URL(request.url()).pathname.match(/^\/api\/(user-locker-messages|user-inbox|reminding-cards)\/([^/]+)\/read$/);
    if (request.method() !== 'PUT' || !match) return;
    const row = {id: decodeURIComponent(match[2]), kind: match[1], status: null,
      finished: false, failed: false, bodyDone: false, bodyValid: false};
    this.rows.push(row);
    this.byRequest.set(request, row);
  }
  response(response) {
    const row = this.byRequest.get(response.request());
    if (!row) return;
    row.status = response.status();
    Promise.resolve().then(() => response.json()).then(body => {
      const item = body.message ?? body.card;
      row.bodyValid = item?.id === row.id && item?.read === true;
    }).catch(() => { row.bodyValid = false; }).finally(() => { row.bodyDone = true; });
  }
  finished(request) { const row = this.byRequest.get(request); if (row) row.finished = true; }
  failed(request) { const row = this.byRequest.get(request); if (row) row.failed = true; }
  summary() {
    const failures = [];
    const counts = new Map();
    for (const row of this.rows) {
      counts.set(row.id, (counts.get(row.id) ?? 0) + 1);
      if (!this.expected.has(row.id)) failures.push(`unexpected:${row.id}`);
      if (row.failed) failures.push(`transport:${row.id}`);
      if (row.status !== null && row.status !== 200) failures.push(`status:${row.id}:${row.status}`);
      if (row.bodyDone && !row.bodyValid) failures.push(`body:${row.id}`);
    }
    for (const [id, count] of counts) if (count !== 1) failures.push(`duplicate:${id}`);
    const completed = this.rows.filter(row => this.expected.has(row.id) && row.finished && row.bodyDone && row.status === 200 && row.bodyValid && !row.failed).length;
    return {completed, expected: this.expected.size, failures};
  }
}
