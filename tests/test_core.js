// node --test tests/test_core.js
const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const W = require('../docs/core.js');
const root = path.resolve(__dirname, '..');
const b64 = (s) => Buffer.from(s, 'utf8').toString('base64');

function mockFetch(handler) {
  const calls = [];
  const fn = async (url, opts = {}) => {
    const u = new URL(url);
    const call = { method: opts.method || 'GET', path: u.pathname, query: u.search, headers: opts.headers || {}, body: opts.body ? JSON.parse(opts.body) : null };
    calls.push(call);
    const r = await handler(call);
    const status = r.status || 200;
    return { ok: status >= 200 && status < 300, status, json: async () => { if (r.body === undefined) throw new Error('no body'); return r.body; } };
  };
  fn.calls = calls;
  return fn;
}
const noSleep = () => Promise.resolve();

test('geometry parity with the Python engine', () => {
  const cases = JSON.parse(execFileSync('python3', [path.join(__dirname, 'make_fixture.py')], { cwd: root }));
  assert.ok(cases.length > 20);
  for (const c of cases) {
    const box = W.baseBox(c.iw, c.ih, c.off);
    box.forEach((v, i) => assert.ok(Math.abs(v - c.box[i]) < 1e-6, `box ${JSON.stringify(c)} ${box}`));
    assert.ok(Math.abs(W.coverScale(c.iw, c.ih) - c.cover) < 1e-9);
    assert.equal(W.needsUpscale(c.iw, c.ih), c.up);
    assert.equal(W.needsUpscale(c.iw, c.ih, W.KB_END), c.up_kb);
  }
});

test('view <-> image mapping round-trips', () => {
  for (const [iw, ih] of [[4000, 3000], [3000, 4000]]) {
    for (const off of [[0.5, 0.5], [0, 1], [0.2, 0.7]]) {
      for (const [px, py] of [[0.1, 0.1], [0.5, 0.5], [0.9, 0.8]]) {
        const f = W.viewToImage(iw, ih, off, px, py);
        const v = W.imageToView(iw, ih, off, f[0], f[1]);
        assert.ok(Math.abs(v[0] - px) < 1e-9 && Math.abs(v[1] - py) < 1e-9);
      }
    }
  }
});

test('buildSelection is compact and the Python parser accepts it', () => {
  const sid = '20261021-101500-ab12';
  const sel = W.buildSelection({
    sid, theme: 'blue', skipped: ['bad word'],
    words: [
      { word: 'umbrella stand', pick: { photoId: 1001, focus: [0.41234, 0.6], offset: [0.5, 0.5] } },
      { word: 'ladle', pick: { upload: `sessions/${sid}/uploads/abc.jpg`, focus: [0.5, 0.25], offset: [0.2, 0.9] } },
      { word: 'wood chisel', pick: { photoId: 33, focus: [1, 0], offset: [0.5, 0.5] } },
    ],
  });
  assert.ok(sel.length < 600, 'compact');
  const out = JSON.parse(execFileSync('python3', [path.join(__dirname, 'parse_sel.py'), '3'], { cwd: root, input: sel }));
  assert.equal(out.sid, sid);
  assert.equal(out.theme, 'blue');
  assert.deepEqual(out.skipped, ['bad word']);
  assert.deepEqual(out.picks.map((p) => p.w), ['umbrella stand', 'ladle', 'wood chisel']);
  assert.equal(out.picks[0].p, 1001);
  assert.deepEqual(out.picks[0].f, [0.412, 0.6]);
  assert.equal(out.picks[1].u, `sessions/${sid}/uploads/abc.jpg`);
  assert.deepEqual(out.picks[1].o, [0.2, 0.9]);
  assert.deepEqual(out.picks[2].f, [1, 0]);
});

test('session ids and repo detection', () => {
  const id = W.newSessionId(new Date(Date.UTC(2026, 9, 21, 9, 5, 7)), () => 0.5);
  assert.match(id, /^20261021-090507-[0-9a-z]{4}$/);
  assert.ok(W.SID_RE.test(id));
  assert.equal(W.detectRepo({ hostname: 'Alice.github.io', pathname: '/wordclip/index.html' }), 'Alice/wordclip');
  assert.equal(W.detectRepo({ hostname: 'alice.github.io', pathname: '/' }), 'alice/alice.github.io');
  assert.equal(W.detectRepo({ hostname: 'localhost', pathname: '/' }), '');
});

test('every engine error code has a Bengali message', () => {
  const fs = require('node:fs');
  const src = fs.readFileSync(path.join(root, 'engine', 'errors.py'), 'utf8');
  const codes = [...src.matchAll(/^[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1]);
  assert.ok(codes.length >= 15);
  for (const c of codes) {
    const m = W.ERROR_MESSAGES[c];
    assert.ok(m && m[0] && m[1], `missing message for ${c}`);
    assert.match(m[0] + m[1], /[\u0980-\u09FF]/, `${c} should be Bengali`);
  }
  assert.deepEqual(W.mapError('nonsense'), W.ERROR_MESSAGES.unknown);
});

test('readState decodes UTF-8 JSON, lists directories and maps 404 to null', async () => {
  const f = mockFetch((c) => {
    if (c.path.endsWith('/sessions')) return { body: [{ name: 'a', type: 'dir' }, { name: 'b', type: 'dir' }] };
    if (c.path.endsWith('status.json')) return { body: { content: b64(JSON.stringify({ state: 'ok', note: 'শব্দ' })) + '\n' } };
    return { status: 404, body: { message: 'Not Found' } };
  });
  const gh = new W.GitHub({ token: 't', repo: 'o/r', fetchFn: f });
  assert.deepEqual(await gh.readStateJson('sessions/x/status.json'), { state: 'ok', note: 'শব্দ' });
  assert.equal((await gh.readStateJson('sessions')).length, 2);
  assert.equal(await gh.readState('sessions/nope/candidates.json'), null);
  const c = f.calls[0];
  assert.match(c.query, /ref=state/);
  assert.equal(c.headers.Authorization, 'Bearer t');
});

test('dispatch finds only the NEW run and sends the right payload', async () => {
  let dispatched = false;
  const f = mockFetch((c) => {
    if (c.method === 'GET' && c.path === '/repos/o/r') return { body: { default_branch: 'trunk' } };
    if (c.method === 'POST') { dispatched = true; return { status: 204 }; }
    if (c.path.endsWith('/runs')) {
      const old = { id: 5, display_title: 'Prepare SID1', html_url: 'old' };
      if (!dispatched) return { body: { workflow_runs: [old] } };
      return { body: { workflow_runs: [old, { id: 7, display_title: 'Prepare OTHER', html_url: 'x' }, { id: 6, display_title: 'Prepare SID1', html_url: 'new' }] } };
    }
    return { status: 500, body: {} };
  });
  const gh = new W.GitHub({ token: 't', repo: 'o/r', fetchFn: f });
  const run = await gh.dispatch('prepare.yml', { session_id: 'SID1', category: '' }, 'SID1', { sleep: noSleep });
  assert.equal(run.id, 6);
  const post = f.calls.find((c) => c.method === 'POST');
  assert.equal(post.path, '/repos/o/r/actions/workflows/prepare.yml/dispatches');
  assert.deepEqual(post.body, { ref: 'trunk', inputs: { session_id: 'SID1', category: '' } });
});

test('dispatch gives up with a clear error when no run appears', async () => {
  const f = mockFetch((c) => {
    if (c.path === '/repos/o/r') return { body: { default_branch: 'main' } };
    if (c.method === 'POST') return { status: 204 };
    return { body: { workflow_runs: [] } };
  });
  const gh = new W.GitHub({ token: 't', repo: 'o/r', fetchFn: f });
  await assert.rejects(() => gh.dispatch('build.yml', {}, 'X', { tries: 3, sleep: noSleep }), (e) => e.message === 'run-not-found');
});

test('waitRun polls until completed and reports ticks', async () => {
  let n = 0;
  const f = mockFetch(() => ({ body: { id: 9, status: ++n < 3 ? 'in_progress' : 'completed', conclusion: 'success' } }));
  const gh = new W.GitHub({ token: 't', repo: 'o/r', fetchFn: f });
  const ticks = [];
  const run = await gh.waitRun(9, (r) => ticks.push(r.status), { sleep: noSleep });
  assert.equal(run.conclusion, 'success');
  assert.deepEqual(ticks, ['in_progress', 'in_progress', 'completed']);
});

test('putState sends base64 to the state branch and retries on 409', async () => {
  let n = 0;
  const f = mockFetch(() => (++n === 1 ? { status: 409, body: { message: 'conflict' } } : { body: { content: { path: 'x' } } }));
  const gh = new W.GitHub({ token: 't', repo: 'o/r', fetchFn: f });
  await gh.putState('sessions/s/uploads/a.jpg', 'QUJD', 'upload');
  assert.equal(f.calls.length, 2);
  assert.deepEqual(f.calls[1].body, { message: 'upload', content: 'QUJD', branch: 'state' });
  assert.equal(f.calls[1].method, 'PUT');
});

test('HTTP errors become GHError with Bengali hints', async () => {
  const f = mockFetch(() => ({ status: 401, body: { message: 'Bad credentials' } }));
  const gh = new W.GitHub({ token: 'bad', repo: 'o/r', fetchFn: f });
  await assert.rejects(() => gh.req('/user'), (e) => e instanceof W.GHError && e.status === 401);
  assert.match(W.httpMessage(401)[0], /Token/);
  assert.match(W.httpMessage(403)[1], /Actions/);
  assert.match(W.httpMessage(404)[0], /Repo/);
  const net = new W.GitHub({ token: 't', repo: 'o/r', fetchFn: async () => { throw new TypeError('offline'); } });
  await assert.rejects(() => net.req('/x'), (e) => e.status === 0);
});

test('checkToken reports each permission separately', async () => {
  const f = mockFetch((c) => (c.path.endsWith('/workflows') ? { status: 403, body: { message: 'x' } } : { body: {} }));
  const gh = new W.GitHub({ token: 't', repo: 'o/r', fetchFn: f });
  const r = await gh.checkToken();
  assert.equal(r.length, 3);
  assert.equal(r[0].ok, true);
  assert.equal(r[1].ok, false);
  assert.match(r[1].hint, /Actions/);
});
