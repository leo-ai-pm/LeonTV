import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: api } = await loadModule('lib/briefPlannerApi.ts');
const { module: control } = await loadModule('lib/briefPlannerController.ts');
const A = 'a'.repeat(32), B = 'b'.repeat(32), BRIEF = 'c'.repeat(32), HASH = 'd'.repeat(64), SNAPSHOT = 'e'.repeat(64);
const production = { duration_seconds: 15, ratio: '9:16', style: 'realistic_cinematic' };
const PRIVATE = 'PRIVATE_SYNTHETIC_SCRIPT_ONLY';
const source = { brief_id: BRIEF, brief_revision: 1, source_version: 1, source_hash: HASH, snapshot_hash: SNAPSHOT, title: '合成简报', production, budget_cny: null, currency: 'CNY', frozen_at: '2026-10-20T00:00:00Z' };
function task(patch = {}) { return { id: A, revision: 1, mode: 'normal', scenario: null, status: 'awaiting_authorization', blocked_reason: 'AUTHORIZATION_REQUIRED', allowed_actions: ['stop'],
  created_at: '2026-10-20T00:00:00Z', updated_at: '2026-10-20T00:00:00Z', source, authorization_status: 'not_authorized', execution_enabled: false, quality_status: 'not_reviewed',
  step_count: 0, repair_count: 0, pending_call: null, plan_version: 0, client_request_key: 'original-create-key', limits: { max_steps: 6, max_repairs: 2, max_output_bytes: 65536, max_context_bytes: 1048576 }, plan: null, plan_versions: [], ...patch }; }
function offline(patch = {}) { return task({ mode: 'offline_test', scenario: 'success', status: 'ready', blocked_reason: null, allowed_actions: ['resume','stop'], authorization_status: 'offline_only', ...patch }); }
function gate(patch = {}) { return { document: { id: BRIEF, revision: 1, is_current: true, script: PRIVATE, requirements: 'PRIVATE_REQUIREMENTS' }, dirty: false, saving: false, loading: false, pending: null, conflictDetected: false, ...patch }; }
function storage() { const values = new Map(); return { values, getItem: k => values.get(k) ?? null, setItem: (k, v) => values.set(k, v), removeItem: k => values.delete(k) }; }
function client(s = storage()) { return new control.BriefPlannerController(() => s); }
function events(after = 0) { return Response.json({ events: [], next_sequence: after, has_more: false }); }
function list(tasks = []) { return Response.json({ tasks, next_offset: null, has_more: false }); }
function write(value, replayed = false, applied = value.revision) { return Response.json({ task: value, replayed, applied_revision: applied }); }
function fail(status, code) { return Response.json({ detail: { code, message: PRIVATE } }, { status }); }
function delayed() { let resolve; return { promise: new Promise(r => { resolve = r; }), release: value => resolve(value) }; }
const settle = () => new Promise(r => setImmediate(r));

test('saved-version gate blocks unsaved/conflicted/loading/history/unconfirmed Briefs before any request', async t => {
  t.mock.method(globalThis, 'fetch', async () => assert.fail('gated creation must not fetch'));
  for (const patch of [{ document: null }, { dirty: true }, { saving: true }, { pending: {} }, { conflictDetected: true }, { loading: true }, { document: { ...gate().document, is_current: false } }]) {
    const c = client(); assert.ok(control.plannerGate(gate(patch))); await c.create(() => gate(patch)); assert.equal(c.getSnapshot().task, null); assert.ok(c.getSnapshot().error);
  }
  assert.equal(control.plannerGate(gate()), null);
});

test('normal creation persists exact references before POST; no body, hash, authorization or automatic action', async t => {
  const s = storage(), c = client(s); let sent, posts = 0, current = gate();
  const wait = delayed();
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (options.method === 'POST') {
      posts++; assert.equal(url, '/api/planner/tasks'); sent = JSON.parse(options.body);
      assert.deepEqual(Object.keys(sent).sort(), ['brief_id','brief_revision','client_request_key']);
      assert.deepEqual(JSON.parse(s.getItem(c.storageKey)).request, sent); assert.equal(JSON.stringify([...s.values]).includes(PRIVATE), false);
      await wait.promise; return write(task({ client_request_key: sent.client_request_key }));
    }
    assert.ok(url.endsWith('/events?after_sequence=0&limit=100')); return events();
  });
  const creating = c.create(() => current); await settle();
  current = gate({ dirty: true, document: { ...gate().document, revision: 2 } });
  await c.create(() => current); wait.release(); await creating;
  assert.equal(posts, 1); assert.equal(c.getSnapshot().task.source.brief_revision, 1); assert.equal(c.getSnapshot().task.status, 'awaiting_authorization');
  assert.equal(c.getSnapshot().task.step_count, 0); assert.equal(s.getItem(c.storageKey), null); assert.equal(current.dirty, true);
});

test('offline creation uses the separate endpoint and scenario, with no resume until explicit action', async t => {
  const c = client(); const posts = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (options.method === 'POST') { const input = JSON.parse(options.body); posts.push(url); assert.equal(input.scenario, 'repair'); return write(offline({ client_request_key: input.client_request_key, scenario: 'repair' })); }
    return events();
  });
  await c.create(() => gate(), 'offline_test', 'repair');
  assert.deepEqual(posts, ['/api/planner/offline-tasks']); assert.equal(c.getSnapshot().task.status, 'ready');
});

test('summary/event adapters retain only safe projections, list never reads source or detail', async t => {
  const urls = [];
  t.mock.method(globalThis, 'fetch', async url => {
    urls.push(url);
    if (url.includes('/events?')) return Response.json({ events: [{ sequence: 1, type: 'task_created', task_revision: 1, created_at: source.frozen_at, step: null, code: null, script: PRIVATE, arguments: { secret: PRIVATE } }], next_sequence: 1, has_more: false });
    return list([task({ script: PRIVATE, source: { ...source, script: PRIVATE } })]);
  });
  const c = client(); await c.loadList(); const summary = c.getSnapshot().tasks[0];
  assert.equal(JSON.stringify(summary).includes(PRIVATE), false); assert.equal('plan' in summary, false); assert.equal('client_request_key' in summary, false);
  assert.equal(urls.length, 1); const page = await api.briefPlannerApi.events(A);
  assert.equal(JSON.stringify(page).includes(PRIVATE), false); assert.equal(page.events[0].sequence, 1);
});

test('lost creation response remount finds original key and ID without posting again', async t => {
  const s = storage(), first = client(s); let sent, posts = 0;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (options.method === 'POST') { posts++; sent = JSON.parse(options.body); throw new TypeError('offline'); }
    if (url.includes('?offset=')) return list();
    if (url.includes('/by-key/')) { assert.ok(url.endsWith(sent.client_request_key)); return Response.json({ task: task() }); }
    if (url.includes('/events?')) return events();
    return Response.json({ task: task({ client_request_key: sent.client_request_key }) });
  });
  await first.create(() => gate()); assert.ok(first.getSnapshot().pending); assert.match(first.getSnapshot().error, /原请求/);
  const restored = client(s); await restored.initialize();
  assert.equal(restored.getSnapshot().task.id, A); assert.equal(restored.getSnapshot().pending, null); assert.equal(posts, 1);
});

test('404/503/network lookups preserve pending references; explicit 404 retry reuses original saved version', async t => {
  const s = storage(), c = client(s); let sent;
  t.mock.method(globalThis, 'fetch', async (_url, opts) => { sent = JSON.parse(opts.body); throw new TypeError('lost'); });
  await c.create(() => gate()); const raw = s.getItem(c.storageKey);
  for (const outcome of [404, 503, 'offline']) {
    t.mock.method(globalThis, 'fetch', async (_url, opts) => { assert.equal(opts.method, 'GET'); if (outcome === 'offline') throw new TypeError('offline'); return fail(outcome, outcome === 404 ? 'NOT_FOUND' : 'PRIVATE_STORAGE_UNAVAILABLE'); });
    await c.recover(false); assert.equal(s.getItem(c.storageKey), raw);
  }
  const methods = [];
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    methods.push(opts.method);
    if (url.includes('/events?')) return events();
    if (opts.method === 'GET') return fail(404, 'NOT_FOUND');
    assert.deepEqual(JSON.parse(opts.body), sent); return write(task({ client_request_key: sent.client_request_key }));
  });
  await c.recover(true); assert.deepEqual(methods, ['GET','POST','GET']); assert.equal(c.getSnapshot().task.source.brief_revision, 1);
});

test('by-key lookup with different binding cannot clear or retarget the original creation', async t => {
  const s = storage(), c = client(s);
  t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('lost'); }); await c.create(() => gate());
  const raw = s.getItem(c.storageKey);
  t.mock.method(globalThis, 'fetch', async () => Response.json({ task: task({ source: { ...source, brief_revision: 2 } }) }));
  await c.recover(false); assert.equal(s.getItem(c.storageKey), raw); assert.equal(c.getSnapshot().task, null);
});

test('uncertain action reads cannot confirm key; explicit replay retains original action/version after newer GET', async t => {
  const s = storage(), c = client(s); let original, server = offline(), writes = 0;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') { writes++; original = JSON.parse(opts.body); throw new TypeError('lost'); }
    return url.includes('/events?') ? events() : Response.json({ task: server });
  });
  await c.open(A); await c.action('resume'); const raw = s.getItem(c.storageKey);
  server = offline({ revision: 4, status: 'stopped', blocked_reason: 'RECOVERY_REQUIRES_RESUME' }); await c.recover(false);
  assert.equal(s.getItem(c.storageKey), raw); assert.equal(c.getSnapshot().task.revision, 4); assert.equal(writes, 1);
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') { assert.deepEqual(JSON.parse(opts.body), original); return write(offline({ revision: 5, status: 'plan_ready', allowed_actions: [] }), true, 2); }
    return events();
  });
  await c.recover(true); assert.equal(c.getSnapshot().pending, null); assert.equal(c.getSnapshot().task.revision, 5);
});

test('action revision conflict refreshes real status, clears rejected intent and never auto-reissues', async t => {
  const c = client(); let current = offline(), writes = 0;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') { writes++; current = offline({ revision: 2, status: 'response_unknown', blocked_reason: 'MODEL_RESPONSE_UNCONFIRMED', allowed_actions: ['stop'] }); return fail(409, 'REVISION_CONFLICT'); }
    return url.includes('/events?') ? events() : Response.json({ task: current });
  });
  await c.open(A); await c.action('resume');
  assert.equal(c.getSnapshot().task.revision, 2); assert.equal(c.getSnapshot().pending, null); assert.match(c.getSnapshot().error, /本次动作未提交/);
  await c.action('resume'); assert.equal(writes, 1);
});

test('unavailable local storage, malformed pending and independent controllers cannot dispatch duplicate new writes', async t => {
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => { calls++; throw new TypeError('lost'); });
  const broken = new control.BriefPlannerController(() => { throw new Error('denied'); }); await broken.create(() => gate()); assert.equal(calls, 0);
  const s = storage(), malformed = client(s); s.setItem(malformed.storageKey, '{bad json'); await malformed.initialize(); await settle();
  const before = calls; await malformed.create(() => gate()); assert.equal(calls, before); assert.equal(malformed.getSnapshot().storageBlocked, true);
  s.removeItem(malformed.storageKey); const one = client(s), two = client(s);
  await Promise.all([one.create(() => gate()), two.create(() => gate())]); assert.equal(calls, before + 1);
});

test('slow reads/writes from a previously selected task cannot replace the selected task or frozen source', async t => {
  const c = client(), wait = delayed(); let posting = false;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') { posting = true; const body = JSON.parse(opts.body); await wait.promise; return write(task({ client_request_key: body.client_request_key })); }
    if (url.includes('/events?')) return events();
    return Response.json({ task: task({ id: B, source: { ...source, brief_revision: 3 } }) });
  });
  const creating = c.create(() => gate()); while (!posting) await settle();
  await c.open(B); wait.release(); await creating;
  assert.equal(c.getSnapshot().task.id, B); assert.equal(c.getSnapshot().task.source.brief_revision, 3); assert.equal(c.getSnapshot().pending, null);
});

test('late detail/source/event responses do not cross task selection; stale revision does not downgrade', async t => {
  const c = client(), wait = delayed(); let delayedSource = false;
  t.mock.method(globalThis, 'fetch', async url => {
    if (url.endsWith('/source')) { delayedSource = true; await wait.promise; return Response.json({ source: { ...source, script: PRIVATE, requirements: 'synthetic' } }); }
    if (url.includes('/events?')) return events();
    return Response.json({ task: task({ id: url.endsWith(B) ? B : A, revision: 3 }) });
  });
  await c.open(A); const reading = c.loadSource(); while (!delayedSource) await settle(); await c.open(B); wait.release(); await reading;
  assert.equal(c.getSnapshot().task.id, B); assert.equal(c.getSnapshot().source, null);
  t.mock.method(globalThis, 'fetch', async url => url.includes('/events?') ? events() : Response.json({ task: task({ id: B, revision: 1 }) }));
  await c.refresh(); assert.equal(c.getSnapshot().task.revision, 3);
});

test('source is fetched only on explicit request, bound to the selected snapshot and never persisted', async t => {
  const s = storage(), c = client(s); const urls = [];
  t.mock.method(globalThis, 'fetch', async url => {
    urls.push(url); if (url.endsWith('/source')) return Response.json({ source: { ...source, script: PRIVATE, requirements: 'synthetic' } });
    return url.includes('/events?') ? events() : Response.json({ task: task() });
  });
  await c.open(A); assert.equal(urls.some(u => u.endsWith('/source')), false); await c.loadSource();
  assert.equal(c.getSnapshot().source.script, PRIVATE); assert.equal(JSON.stringify([...s.values]).includes(PRIVATE), false);
});

test('a list started before creation cannot erase its newly confirmed task', async t => {
  const c = client(), wait = delayed();
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (url.includes('?offset=')) { await wait.promise; return list(); }
    if (opts.method === 'POST') return write(task({ client_request_key: JSON.parse(opts.body).client_request_key }));
    return events();
  });
  const listing = c.loadList(); await c.create(() => gate()); wait.release(); await listing;
  assert.equal(c.getSnapshot().tasks[0].id, A); assert.equal(c.getSnapshot().listLoading, false);
});

test('old slow detail and events cannot overwrite the newly selected task or append its events', async t => {
  const c = client(), wait = delayed(); let delayedDetail = true;
  t.mock.method(globalThis, 'fetch', async url => {
    if (url.endsWith(A) && delayedDetail) { await wait.promise; return Response.json({ task: task() }); }
    if (url.includes('/events?')) return events();
    return Response.json({ task: task({ id: B }) });
  });
  const first = c.open(A); await c.open(B); wait.release(); await first;
  assert.equal(c.getSnapshot().task.id, B);
  const late = delayed(); delayedDetail = false;
  t.mock.method(globalThis, 'fetch', async url => {
    if (url.includes(`/${A}/events?`)) { await late.promise; return Response.json({ events: [{ sequence: 1, type: 'task_created', task_revision: 1, created_at: source.frozen_at, step: null, code: null }], next_sequence: 1, has_more: false }); }
    if (url.includes('/events?')) return events();
    return Response.json({ task: task({ id: url.endsWith(A) ? A : B }) });
  });
  const openingA = c.open(A); await settle(); await c.open(B); late.release(); await openingA;
  assert.equal(c.getSnapshot().task.id, B); assert.deepEqual(c.getSnapshot().events, []);
});

test('structured plans reject cycles, invalid totals and mismatched frozen source; no unsafe authority accepted', () => {
  const plan = { source_snapshot_hash: SNAPSHOT, duration_ms: 15000, ratio: '9:16', shots: [{ id: 'S1', duration_ms: 7500, visual: '<script>synthetic</script>', camera: '', audio: '', depends_on: [] }, { id: 'S2', duration_ms: 7500, visual: 'synthetic', camera: '', audio: '', depends_on: ['S1'] }] };
  assert.equal(api.readPlannerDetail(offline({ plan, plan_version: 1 })).plan.shots[0].visual, '<script>synthetic</script>');
  for (const bad of [{ ...plan, source_snapshot_hash: HASH }, { ...plan, shots: [{ ...plan.shots[0], duration_ms: 15000, depends_on: ['S1'] }] }, { ...plan, shots: [{ ...plan.shots[0], duration_ms: 5 }] }]) assert.throws(() => api.readPlannerDetail(offline({ plan: bad })));
  for (const patch of [{ execution_enabled: true }, { authorization_status: 'authorized' }, { quality_status: 'passed' }]) assert.throws(() => api.readPlannerDetail(task(patch)));
});

test('fetch deadline releases controller lock but keeps unknown request for same-key recovery', async t => {
  let deadline, cleared = 0; const c = client();
  t.mock.method(globalThis, 'setTimeout', fn => { deadline = fn; return 123; });
  t.mock.method(globalThis, 'clearTimeout', () => { cleared++; });
  t.mock.method(globalThis, 'fetch', async (_url, opts) => new Promise((_resolve, reject) => { opts.signal.addEventListener('abort', () => reject(new DOMException('aborted','AbortError'))); }));
  const creating = c.create(() => gate()); await settle(); deadline(); await creating;
  assert.equal(c.getSnapshot().busy, false); assert.ok(c.getSnapshot().pending); assert.match(c.getSnapshot().error, /原请求/); assert.equal(cleared, 1);
});
