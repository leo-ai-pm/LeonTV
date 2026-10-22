import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: api } = await loadModule('lib/plannerExecutionApi.ts');
const { module: control } = await loadModule('lib/plannerExecutionController.ts');
const { module: types } = await loadModule('lib/plannerExecutionTypes.ts');
const A = 'a'.repeat(32), B = 'b'.repeat(32), BRIEF = 'c'.repeat(32), HASH = 'd'.repeat(64), SNAP = 'e'.repeat(64), SNAP_B = 'f'.repeat(64);
const Q = '1'.repeat(32), G = '2'.repeat(32), OP = '3'.repeat(32), Q_B = '4'.repeat(32), Q_NEW = '5'.repeat(32);
const WHEN = '2026-10-20T00:00:00Z', EXPIRES = '2099-10-20T00:00:00Z', PRIVATE = 'SYNTHETIC_PRIVATE_WIRE_AND_KEY';
const source = { brief_id: BRIEF, brief_revision: 1, source_version: 1, source_hash: HASH, snapshot_hash: SNAP, title: '合成冻结简报', production: { duration_seconds: 15, ratio: '9:16', style: 'realistic_cinematic' }, budget_cny: null, currency: 'CNY', frozen_at: WHEN };
const policy = { id: 'synthetic-test-policy', version: 'v1', policy_hash: HASH, provider: 'openai_compatible', model: 'synthetic-only', endpoint: 'https://synthetic.invalid/v1/chat/completions', currency: 'CNY',
  input_cny_per_million: '0.500000', output_cny_per_million: '1.000000', max_input_tokens: 1000, max_output_tokens: 1000, max_input_bytes: 32768, max_output_bytes: 65536,
  request_deadline_seconds: 10, max_calls: 6, task_limit_cny: '1.000000', account_limit_cny: '2.000000', account_max_calls: 12, valid_until: EXPIRES,
  privacy_scope: types.EXECUTION_SCOPE, pricing_basis: 'synthetic test only, not verified real prices', input_bound_basis: 'synthetic bound only' };
function quote(patch = {}) { return { id: Q, task_id: A, planner_task_revision: 1, snapshot_hash: SNAP, policy, per_call_hold_cny: '0.001500', total_limit_cny: '1.000000', max_calls: 6, expires_at: EXPIRES, disclosure_hash: HASH, created_at: WHEN, status: 'valid', ...patch }; }
function grant(patch = {}) { return { id: G, task_id: A, quote_id: Q, snapshot_hash: SNAP, policy_hash: HASH, disclosure_hash: HASH, scope: types.EXECUTION_SCOPE, status: 'active', total_limit_cny: '1.000000', max_calls: 6, expires_at: EXPIRES, created_at: WHEN, revoked_at: null, ...patch }; }
function operation(patch = {}) { return { id: OP, step: 1, status: 'submitting', grant_id: G, quote_id: Q, policy_hash: HASH, context_hash: HASH, hold_cny: '0.001500', locally_accounted_cny: null, provider_request_id: null, provider_response_id: null, input_tokens: null, output_tokens: null, finish_reason: null, receipt_status: 'none', created_at: WHEN, received_at: null, ...patch }; }
function execution(patch = {}) { const taskId = patch.task_id || A; return { task_id: taskId, revision: 0, environment: 'synthetic_test', status: 'not_authorized', blocked_reason: 'AUTHORIZATION_REQUIRED', allowed_actions: [],
  source: taskId === B ? { ...source, snapshot_hash: SNAP_B } : source, policy_available: true, active_grant_id: null, quotes: [], grants: [],
  ledger: { currency: 'CNY', locally_accounted_cny: '0.000000', held_cny: '0.000000', task_limit_cny: null, call_count: 0, max_calls: 0,
    account_locally_accounted_cny: '0.000000', account_held_cny: '0.000000', account_limit_cny: null, account_call_count: 0, account_max_calls: 0, provider_bill_confirmed: false },
  operations: [], plan: null, quality_status: 'not_reviewed', created_at: null, updated_at: null, ...patch }; }
function binding(taskId = A, patch = {}) { return { id: taskId, revision: 1, mode: 'normal', source: taskId === B ? { ...source, snapshot_hash: SNAP_B } : source, ...patch }; }
function ready(patch = {}) { return execution({ revision: 2, status: 'ready', blocked_reason: null, allowed_actions: ['resume'], active_grant_id: G, quotes: [quote()], grants: [grant()], ...patch }); }
function store() { const values = new Map(); return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }; }
const client = (storage = store()) => new control.PlannerExecutionController(() => storage);
const policyView = (patch = {}) => ({ available: true, blocked_reason: null, environment: 'synthetic_test', policy, ...patch });
const result = (e, resource = {}, replayed = false, applied = e.revision) => Response.json({ execution: e, ...resource, replayed, applied_revision: applied });
const failure = (status, code) => Response.json({ detail: { code, message: PRIVATE } }, { status });
function events(url) { const after = Number(String(url).match(/after_sequence=(\d+)/)?.[1] || 0); return Response.json({ events: [], next_sequence: after, has_more: false }); }
function reads(url, e, p = policyView()) { return url.endsWith('/execution/policy') ? Response.json(p) : url.includes('/events?') ? events(url) : Response.json({ execution: e }); }
const settle = () => new Promise(resolve => setImmediate(resolve));
function delayed() { let resolve; return { promise: new Promise(r => { resolve = r; }), release: value => resolve(value) }; }

test('disabled and unavailable policy permit only observation; offline tasks never call D09', async t => {
  const c = client(); let writes = 0;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') writes++;
    return reads(url, execution({ environment: 'disabled', policy_available: false, blocked_reason: 'LIVE_DISABLED' }), policyView({ available: false, environment: 'disabled', blocked_reason: 'LIVE_DISABLED', policy: null }));
  });
  await c.initialize(binding()); await c.requestQuote(); await c.grant(); await c.action('resume'); assert.equal(writes, 0); assert.equal(c.getSnapshot().execution.revision, 0);
  t.mock.method(globalThis, 'fetch', async () => assert.fail('offline must not call D09'));
  const offline = client(); await offline.initialize(binding(A, { mode: 'offline_test' })); await offline.requestQuote(); await offline.grant(); await offline.action('resume'); assert.equal(offline.getSnapshot().execution, null);
});

test('quote persists exact revision references before POST and never includes prices/body/model or grants automatically', async t => {
  const s = store(), c = client(s), old = binding(); let current = execution(), posted;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') {
      assert.ok(url.endsWith('/quotes')); posted = JSON.parse(opts.body);
      assert.deepEqual(Object.keys(posted).sort(), ['client_request_key','expected_execution_revision','expected_task_revision']);
      assert.deepEqual(JSON.parse(s.getItem(c.storageKey)).request, posted); assert.equal(JSON.stringify([...s.values]).includes(PRIVATE), false);
      current = execution({ revision: 1, quotes: [quote()] }); return result(current, { quote: quote() });
    }
    return reads(url, current);
  });
  await c.initialize(old); await c.requestQuote(); assert.equal(posted.expected_task_revision, 1); assert.equal(posted.expected_execution_revision, 0);
  assert.equal(c.getSnapshot().reviewed, null); assert.equal(c.getSnapshot().execution.grants.length, 0); assert.equal(c.getSnapshot().pending, null); assert.equal(old.revision, 1);
});

test('grant requires explicit current disclosure review; success never starts until separate resume action', async t => {
  const c = client(); let current = execution({ revision: 1, quotes: [quote()] }); const posts = [];
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') {
      const body = JSON.parse(opts.body); posts.push({ url, body });
      if (url.endsWith('/grants')) {
        assert.deepEqual(Object.keys(body).sort(), ['accepted_disclosure_hash','accepted_scope','client_request_key','consent','expected_execution_revision','quote_id']);
        assert.equal(body.consent, true); assert.equal(body.accepted_scope, types.EXECUTION_SCOPE); assert.equal(body.quote_id, Q); assert.equal(body.accepted_disclosure_hash, HASH); assert.equal(body.expected_execution_revision, 1);
        current = ready(); return result(current, { grant: grant() });
      }
      assert.equal(body.action, 'resume'); assert.equal(body.expected_execution_revision, 2);
      current = ready({ revision: 3, status: 'running', allowed_actions: ['stop'], operations: [operation()], ledger: { ...current.ledger, held_cny: '0.001500', call_count: 1 } });
      return result(current);
    }
    return reads(url, current);
  });
  await c.initialize(binding()); await c.grant(); assert.equal(posts.length, 0); assert.match(c.getSnapshot().error, /明确核对/);
  await c.refresh(); c.reviewQuote(true); await c.grant(); assert.equal(posts.length, 1); assert.equal(c.getSnapshot().execution.status, 'ready'); assert.equal(c.getSnapshot().reviewed, null);
  await c.action('resume'); assert.equal(posts.length, 2); assert.equal(c.getSnapshot().execution.status, 'running'); assert.equal(c.getSnapshot().execution.ledger.held_cny, '0.001500');
});

test('review is invalidated by quote choice, D09 revision and D08 task revision changes', async t => {
  const c = client(); let current = execution({ revision: 1, quotes: [quote(), quote({ id: Q_NEW })] });
  t.mock.method(globalThis, 'fetch', async (url, opts) => { assert.equal(opts.method, 'GET'); return reads(url, current); });
  await c.initialize(binding()); c.reviewQuote(true); assert.ok(c.getSnapshot().reviewed);
  c.chooseQuote(Q); assert.equal(c.getSnapshot().reviewed, null); c.reviewQuote(true);
  current = { ...current, revision: 2 }; await c.refresh(); assert.equal(c.getSnapshot().reviewed, null);
  c.reviewQuote(true); await c.select(binding(A, { revision: 2 })); assert.equal(c.getSnapshot().reviewed, null); assert.match(c.quoteGate(), /任务版本不一致/);
});

test('expired/stale policy or mismatched quote cannot be reviewed or authorized', async t => {
  for (const q of [quote({ status: 'expired' }), quote({ expires_at: WHEN }), quote({ policy: { ...policy, valid_until: WHEN } }), quote({ policy: { ...policy, policy_hash: SNAP_B } }), quote({ planner_task_revision: 2 })]) {
    const c = client(); t.mock.method(globalThis, 'fetch', async (url, opts) => { assert.equal(opts.method, 'GET'); return reads(url, execution({ revision: 1, quotes: [q] })); });
    await c.initialize(binding()); c.reviewQuote(true); assert.equal(c.getSnapshot().reviewed, null); await c.grant(); assert.ok(c.getSnapshot().error);
  }
});

test('lost quote survives remount; readonly by-key recovery never posts or silently uses a new task revision', async t => {
  const s = store(), first = client(s); let input, writes = 0, current = execution();
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') { input = JSON.parse(opts.body); writes++; current = execution({ revision: 1, quotes: [quote()] }); throw new TypeError('lost after commit'); }
    if (url.includes('/quotes/by-key/')) { assert.ok(url.endsWith(input.client_request_key)); return Response.json({ quote: quote() }); }
    return reads(url, current);
  });
  await first.initialize(binding()); await first.requestQuote(); const original = s.getItem(first.storageKey); assert.ok(original);
  const restored = client(s); await restored.initialize(binding(A, { revision: 2 })); await restored.recover(false);
  assert.equal(writes, 1); assert.equal(restored.getSnapshot().pending, null); assert.equal(restored.getSnapshot().quoteId, Q); assert.match(restored.quoteGate(), /任务版本不一致/);
});

test('quote lookup 404/503/network retains original request; explicit retry after 404 uses original exact key/body', async t => {
  const c = client(); let input;
  t.mock.method(globalThis, 'fetch', async (url, opts) => { if (opts.method === 'POST') { input = JSON.parse(opts.body); throw new TypeError('lost'); } return reads(url, execution()); });
  await c.initialize(binding()); await c.requestQuote(); const original = c.getSnapshot().pending;
  for (const outcome of [404, 503, 'offline']) {
    t.mock.method(globalThis, 'fetch', async (_url, opts) => { assert.equal(opts.method, 'GET'); if (outcome === 'offline') throw new TypeError('offline'); return failure(outcome, outcome === 404 ? 'NOT_FOUND' : 'PRIVATE_STORAGE_UNAVAILABLE'); });
    await c.recover(false); assert.deepEqual(c.getSnapshot().pending, original);
  }
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (url.includes('/by-key/')) return failure(404, 'NOT_FOUND');
    if (opts.method === 'POST') { assert.deepEqual(JSON.parse(opts.body), input); return result(execution({ revision: 1, quotes: [quote()] }), { quote: quote() }); }
    return events(url);
  });
  await c.recover(true); assert.equal(c.getSnapshot().pending, null);
});

for (const kind of ['quote','grant','action','revoke']) test(`uncertain ${kind} replay confirms original A while selected B, preserving B quote/ledger without false error`, async t => {
  const c = client(); let original, writes = 0;
  const qb = quote({ id: Q_B, task_id: B, snapshot_hash: SNAP_B });
  const b = execution({ task_id: B, revision: 8, quotes: [qb], ledger: { ...execution().ledger, held_cny: '0.004000' } });
  const start = kind === 'quote' ? execution() : kind === 'grant' ? execution({ revision: 1, quotes: [quote()] }) : ready();
  const applied = kind === 'quote' ? execution({ revision: 1, quotes: [quote({ id: Q_NEW })] }) : kind === 'grant' ? ready() : ready({ revision: 4, status: 'stopped', allowed_actions: [], ledger: { ...ready().ledger, held_cny: '0.001500', call_count: 1 } });
  const resource = kind === 'quote' ? { quote: quote({ id: Q_NEW }) } : kind === 'grant' ? { grant: grant() } : kind === 'revoke' ? { grant: grant({ status: 'revoked', revoked_at: WHEN }) } : {};
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (url.includes('/by-key/')) return failure(404, 'NOT_FOUND');
    if (opts.method === 'POST') {
      writes++; if (writes === 1) { original = { url, body: JSON.parse(opts.body) }; throw new TypeError('uncertain'); }
      assert.equal(url, original.url); assert.deepEqual(JSON.parse(opts.body), original.body); return result(applied, resource, true, 3);
    }
    return reads(url, url.includes(`/${B}/`) ? b : start);
  });
  await c.initialize(binding());
  if (kind === 'quote') await c.requestQuote(); else if (kind === 'grant') { c.reviewQuote(true); await c.grant(); } else if (kind === 'action') await c.action('resume'); else await c.revoke(G);
  assert.ok(c.getSnapshot().pending); await c.select(binding(B)); const before = c.getSnapshot().execution;
  await c.recover(true);
  assert.equal(writes, 2); assert.equal(c.getSnapshot().pending, null); assert.equal(c.getSnapshot().error, ''); assert.match(c.getSnapshot().notice, /原任务请求.*当前任务选择保持/);
  assert.deepEqual(c.getSnapshot().execution, before); assert.equal(c.getSnapshot().quoteId, Q_B); assert.equal(c.getSnapshot().execution.ledger.held_cny, '0.004000');
});

test('unknown grant readonly lookup confirms resource without starting or switching the current task', async t => {
  const s = store(), c = client(s); let writes = 0, original;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') { writes++; original = JSON.parse(opts.body); throw new TypeError('lost'); }
    if (url.includes('/grants/by-key/')) { assert.ok(url.endsWith(original.client_request_key)); return Response.json({ grant: grant() }); }
    return reads(url, url.includes(`/${B}/`) ? execution({ task_id: B }) : execution({ revision: 1, quotes: [quote()] }));
  });
  await c.initialize(binding()); c.reviewQuote(true); await c.grant(); await c.select(binding(B)); await c.recover(false);
  assert.equal(writes, 1); assert.equal(c.getSnapshot().pending, null); assert.equal(c.getSnapshot().execution.task_id, B); assert.equal(c.getSnapshot().execution.grants.length, 0);
});

test('unknown action query retains original action/revision and hold; exact replay cannot retarget to newer revision', async t => {
  const c = client(); let current = ready(), original, posts = 0;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') { original = JSON.parse(opts.body); posts++; current = ready({ revision: 5, status: 'needs_reconciliation', blocked_reason: 'RESPONSE_UNCONFIRMED', allowed_actions: ['stop'], operations: [operation()], ledger: { ...current.ledger, held_cny: '0.001500', call_count: 1 } }); throw new TypeError('lost'); }
    return reads(url, current);
  });
  await c.initialize(binding()); await c.action('resume'); const saved = c.getSnapshot().pending; await c.recover(false);
  assert.deepEqual(c.getSnapshot().pending, saved); assert.equal(posts, 1); assert.equal(c.getSnapshot().execution.ledger.held_cny, '0.001500'); assert.equal(saved.request.expected_execution_revision, 2);
  t.mock.method(globalThis, 'fetch', async (url, opts) => { if (opts.method === 'POST') { posts++; assert.deepEqual(JSON.parse(opts.body), original); return result(current, {}, true, 3); } return reads(url, current); });
  await c.recover(true); assert.equal(posts, 2); assert.equal(c.getSnapshot().pending, null); assert.equal(c.getSnapshot().execution.revision, 5); assert.equal(c.getSnapshot().execution.ledger.held_cny, '0.001500');
});

test('unconfirmed calls block resume even with stale allowed actions; no receipt forbids reconcile without freeing hold', async t => {
  const c = client(), current = ready({ revision: 3, status: 'needs_reconciliation', blocked_reason: 'RESPONSE_UNCONFIRMED', allowed_actions: ['stop','resume','reconcile'], operations: [operation()], ledger: { ...ready().ledger, held_cny: '0.001500', call_count: 1 } });
  t.mock.method(globalThis, 'fetch', async (url, opts) => { assert.equal(opts.method, 'GET'); return reads(url, current); });
  await c.initialize(binding()); await c.action('resume'); assert.match(c.getSnapshot().error, /未确认调用/);
  await c.refresh(); await c.action('reconcile'); assert.match(c.getSnapshot().error, /没有可处理的持久回执/); assert.equal(c.getSnapshot().execution.ledger.held_cny, '0.001500');
});

test('reconcile only submits exact action reference, never client usage, receipt, price or authority', async t => {
  const c = client(), current = ready({ revision: 3, status: 'needs_reconciliation', blocked_reason: 'RECEIPT_INCOMPLETE', allowed_actions: ['reconcile'], operations: [operation({ status: 'needs_reconciliation', receipt_status: 'unconfirmed', received_at: WHEN })], ledger: { ...ready().ledger, held_cny: '0.001500', call_count: 1 } });
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') { const input = JSON.parse(opts.body); assert.deepEqual(Object.keys(input).sort(), ['action','client_action_key','expected_execution_revision']); assert.equal(input.action, 'reconcile'); return result({ ...current, revision: 4 }); }
    return reads(url, current);
  });
  await c.initialize(binding()); await c.action('reconcile'); assert.equal(c.getSnapshot().execution.ledger.held_cny, '0.001500'); assert.equal(c.getSnapshot().execution.operations[0].locally_accounted_cny, null);
});

test('revocation remains available when policy is disabled and does not clear unresolved hold or usage', async t => {
  const c = client(), current = ready({ policy_available: false, status: 'blocked', blocked_reason: 'LIVE_DISABLED', ledger: { ...ready().ledger, held_cny: '0.001500', call_count: 1 }, operations: [operation()] }); let posts = 0;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') { posts++; assert.ok(url.endsWith(`/grants/${G}/revoke`)); return result({ ...current, revision: 3, grants: [grant({ status: 'revoked', revoked_at: WHEN })] }, { grant: grant({ status: 'revoked', revoked_at: WHEN }) }); }
    return reads(url, current, policyView({ available: false, blocked_reason: 'LIVE_DISABLED', policy: null, environment: 'disabled' }));
  });
  await c.initialize(binding()); await c.revoke(G); assert.equal(posts, 1); assert.equal(c.getSnapshot().execution.ledger.held_cny, '0.001500'); assert.equal(c.getSnapshot().execution.ledger.call_count, 1);
});

test('revision conflict clears rejected key, refreshes actual hold and never reissues a new action automatically', async t => {
  const c = client(); let current = ready(), posts = 0;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') { posts++; current = ready({ revision: 8, status: 'needs_reconciliation', blocked_reason: 'RESPONSE_UNCONFIRMED', allowed_actions: ['stop'], ledger: { ...ready().ledger, held_cny: '0.001500' }, operations: [operation()] }); return failure(409, 'REVISION_CONFLICT'); }
    return reads(url, current);
  });
  await c.initialize(binding()); await c.action('resume'); assert.equal(posts, 1); assert.equal(c.getSnapshot().pending, null); assert.equal(c.getSnapshot().execution.revision, 8); assert.equal(c.getSnapshot().execution.ledger.held_cny, '0.001500'); assert.match(c.getSnapshot().error, /本次新动作未提交/);
});

test('KEY_CONFLICT and mismatched by-key source do not clear or retarget original request', async t => {
  const c = client();
  t.mock.method(globalThis, 'fetch', async (url, opts) => opts.method === 'POST' ? failure(409, 'KEY_CONFLICT') : reads(url, execution()));
  await c.initialize(binding()); await c.requestQuote(); const original = c.getSnapshot().pending; assert.ok(original);
  t.mock.method(globalThis, 'fetch', async () => Response.json({ quote: quote({ snapshot_hash: SNAP_B }) }));
  await c.recover(false); assert.deepEqual(c.getSnapshot().pending, original); assert.equal(c.getSnapshot().execution.revision, 0);
});

test('storage failures and independent controllers stop duplicate writes before dispatch', async t => {
  const s = store(), one = client(s), two = client(s); let posts = 0;
  t.mock.method(globalThis, 'fetch', async (url, opts) => { if (opts.method === 'POST') { posts++; throw new TypeError('lost'); } return reads(url, execution()); });
  await one.initialize(binding()); await two.initialize(binding()); await Promise.all([one.requestQuote(), two.requestQuote()]); assert.equal(posts, 1);
  const broken = new control.PlannerExecutionController(() => { throw new Error('blocked'); }); await broken.initialize(binding()); await broken.requestQuote(); assert.equal(posts, 1); assert.equal(broken.getSnapshot().storageBlocked, true);
});

test('late reads and writes from A cannot replace B; older execution revision never downgrades the view', async t => {
  const c = client(), wait = delayed(); let late = false;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') { late = true; await wait.promise; return result(execution({ revision: 1, quotes: [quote()] }), { quote: quote() }); }
    return reads(url, url.includes(`/${B}/`) ? execution({ task_id: B, revision: 7 }) : execution());
  });
  await c.initialize(binding()); const creating = c.requestQuote(); while (!late) await settle(); await c.select(binding(B)); wait.release(); await creating;
  assert.equal(c.getSnapshot().execution.task_id, B); assert.equal(c.getSnapshot().execution.revision, 7); assert.equal(c.getSnapshot().quoteId, null);
  t.mock.method(globalThis, 'fetch', async url => reads(url, execution({ task_id: B, revision: 2 })));
  await c.refresh(); assert.equal(c.getSnapshot().execution.revision, 7);
});

test('late view/events and a development remount cannot cross task selection or strand a loading state', async t => {
  const c = client(), wait = delayed(); let count = 0;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    assert.equal(opts.method, 'GET');
    if (url.endsWith(`/${A}/execution`)) { count++; const snapshot = count === 1 ? 1 : 2; if (count === 1) await wait.promise; return Response.json({ execution: execution({ revision: snapshot }) }); }
    return reads(url, execution({ revision: count === 1 ? 1 : 2 }));
  });
  const initial = c.initialize(binding()); await settle(); c.invalidate(); await c.initialize(binding()); wait.release(); await initial;
  assert.equal(c.getSnapshot().execution.revision, 2); assert.equal(c.getSnapshot().loading, false);
  const lateEvents = delayed(); let reading = false;
  t.mock.method(globalThis, 'fetch', async url => {
    if (url.includes(`/${A}/execution/events?`)) { reading = true; await lateEvents.promise; return Response.json({ events: [{ sequence: 1, type: 'quote_created', execution_revision: 1, created_at: WHEN, operation_id: null, code: null }], next_sequence: 1, has_more: false }); }
    return reads(url, execution({ task_id: B, revision: 4 }));
  });
  const oldEvents = c.loadEvents(); while (!reading) await settle(); await c.select(binding(B)); lateEvents.release(); await oldEvents;
  assert.equal(c.getSnapshot().execution.task_id, B); assert.deepEqual(c.getSnapshot().events, []);
});

for (const readFirst of [true, false]) test(`same-binding parent refresh preserves deferred resume ACK (${readFirst ? 'old GET first' : 'ACK first'})`, async t => {
  const c = client(), ack = delayed(), read = delayed(), task = binding();
  let gets = 0, posts = 0, posting = false;
  const queued = ready({ revision: 3, status: 'queued', allowed_actions: ['stop'], operations: [operation()], ledger: { ...ready().ledger, held_cny: '0.001500', call_count: 1 } });
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') { posts++; posting = true; await ack.promise; return result(queued); }
    if (url.endsWith(`/${A}/execution`)) { gets++; if (gets === 2 && !readFirst) await read.promise; return Response.json({ execution: ready() }); }
    return reads(url, ready());
  });
  await c.initialize(task);
  const submitting = c.action('resume'); while (!posting) await settle();
  const refreshingParent = c.initialize({ ...task, source: { ...task.source } });
  if (readFirst) await refreshingParent;
  else while (gets < 2) await settle();
  ack.release(); await submitting;
  if (!readFirst) { read.release(); await refreshingParent; }
  assert.equal(c.getSnapshot().execution.revision, 3); assert.equal(c.getSnapshot().execution.status, 'queued');
  assert.equal(c.getSnapshot().execution.ledger.held_cny, '0.001500'); assert.equal(c.getSnapshot().pending, null);
  assert.equal(c.getSnapshot().busy, false); assert.equal(c.getSnapshot().loading, false); assert.equal(c.getSnapshot().error, '');
  assert.match(c.getSnapshot().notice, /规划操作已确认/); assert.equal(posts, 1);
});

test('StrictMode cleanup/setup on the same binding restarts observation and discards the old slow GET', async t => {
  const c = client(), old = delayed(); let gets = 0;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    assert.equal(opts.method, 'GET');
    if (url.endsWith(`/${A}/execution`)) { gets++; const captured = gets; if (captured === 1) await old.promise; return Response.json({ execution: ready({ revision: captured === 1 ? 2 : 4 }) }); }
    return reads(url, ready());
  });
  const beforeCleanup = c.initialize(binding()); await settle(); c.invalidate();
  await c.initialize({ ...binding() }); old.release(); await beforeCleanup;
  assert.equal(c.getSnapshot().execution.revision, 4); assert.equal(c.getSnapshot().loading, false);
  assert.equal(c.getSnapshot().busy, false); assert.equal(c.getSnapshot().error, ''); assert.equal(c.getSnapshot().pending, null);
});

test('polling during a deferred same-binding initialize waits for it and resumes without stranding loading', async t => {
  const c = client(), read = delayed(); let gets = 0;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    assert.equal(opts.method, 'GET');
    if (url.endsWith(`/${A}/execution`)) {
      gets++; const captured = gets;
      if (captured === 2) await read.promise;
      return Response.json({ execution: ready({ revision: captured >= 3 ? 4 : 3, status: 'running', allowed_actions: ['stop'] }) });
    }
    return reads(url, ready());
  });
  const task = binding(); await c.initialize(task);
  const parentRefresh = c.initialize({ ...task, source: { ...task.source } }); await settle();
  assert.equal(c.getSnapshot().loading, true);
  await c.refresh(); read.release(); await parentRefresh;
  assert.equal(c.getSnapshot().loading, false); assert.equal(c.getSnapshot().refreshing, false); assert.equal(c.getSnapshot().error, '');
  assert.equal(gets, 2);
  await c.refresh();
  assert.equal(c.getSnapshot().execution.revision, 4); assert.equal(c.getSnapshot().execution.status, 'running');
  assert.equal(c.getSnapshot().loading, false); assert.equal(c.getSnapshot().refreshing, false); assert.equal(gets, 3);
});

for (const detach of ['source', 'unmount']) test(`${detach} change still isolates a deferred operation ACK`, async t => {
  const c = client(), ack = delayed(); let posting = false, changed = false;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') { posting = true; await ack.promise; return result(ready({ revision: 3, status: 'queued', allowed_actions: ['stop'] })); }
    return reads(url, changed ? execution({ revision: 8, source: { ...source, snapshot_hash: SNAP_B } }) : ready());
  });
  await c.initialize(binding()); const submitting = c.action('resume'); while (!posting) await settle();
  if (detach === 'source') { changed = true; await c.select(binding(A, { source: { ...source, snapshot_hash: SNAP_B } })); }
  else c.invalidate();
  ack.release(); await submitting;
  assert.equal(c.getSnapshot().execution.revision, detach === 'source' ? 8 : 2);
  assert.equal(c.getSnapshot().notice, ''); assert.equal(c.getSnapshot().error, ''); assert.equal(c.getSnapshot().pending, null);
});

test('new grant keeps the cumulative calls and unresolved hold; it cannot make an unknown call resumable', async t => {
  const c = client(); const ledger = { ...ready().ledger, held_cny: '0.001500', locally_accounted_cny: '0.000100', call_count: 2 };
  const current = ready({ revision: 4, status: 'needs_reconciliation', blocked_reason: 'RESPONSE_UNCONFIRMED', allowed_actions: ['stop'], ledger, operations: [operation()] });
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') {
      assert.ok(url.endsWith('/grants')); const fresh = grant({ id: Q_NEW });
      return result({ ...current, revision: 5, grants: [...current.grants, fresh], active_grant_id: fresh.id }, { grant: fresh });
    }
    return reads(url, current);
  });
  await c.initialize(binding()); c.reviewQuote(true); await c.grant();
  assert.deepEqual(c.getSnapshot().execution.ledger, ledger); assert.equal(control.executionHasUnknownCall(c.getSnapshot().execution), true);
});

test('adapter keeps null unknown money/usage, drops raw/private fields and rejects unsafe endpoint/billing/plan binding', async t => {
  const response = ready({ operations: [operation({ raw: PRIVATE, content: PRIVATE })], raw: PRIVATE, source: { ...source, script: PRIVATE }, quotes: [quote({ policy: { ...policy, api_key: PRIVATE } })] });
  const safe = api.readExecutionDetail(response); assert.equal(JSON.stringify(safe).includes(PRIVATE), false);
  assert.equal(safe.operations[0].locally_accounted_cny, null); assert.equal(safe.operations[0].input_tokens, null); assert.equal(safe.operations[0].provider_request_id, null);
  assert.throws(() => api.readExecutionDetail(ready({ ledger: { ...ready().ledger, provider_bill_confirmed: true } })));
  assert.throws(() => api.readExecutionPolicy({ ...policy, endpoint: 'https://synthetic.invalid/v1/chat/completions?api_key=synthetic' }));
  assert.throws(() => api.readExecutionDetail(ready({ quotes: [quote({ task_id: B })] })));
  assert.throws(() => api.readExecutionDetail(ready({ plan: { source_snapshot_hash: SNAP_B, duration_ms: 15000, ratio: '9:16', shots: [] } })));
  t.mock.method(globalThis, 'fetch', async () => Response.json({ events: [{ sequence: 1, type: 'intent_saved', execution_revision: 3, created_at: WHEN, operation_id: OP, code: null, raw: PRIVATE }], next_sequence: 1, has_more: false }));
  assert.equal(JSON.stringify(await api.plannerExecutionApi.events(A)).includes(PRIVATE), false);
});

test('POST timeout releases lock but preserves original key, exact references and existing hold', async t => {
  const c = client(); t.mock.method(globalThis, 'fetch', async url => reads(url, ready())); await c.initialize(binding());
  let deadline, cleared = 0;
  t.mock.method(globalThis, 'setTimeout', fn => { deadline = fn; return 123; }); t.mock.method(globalThis, 'clearTimeout', () => { cleared++; });
  t.mock.method(globalThis, 'fetch', async (_url, opts) => new Promise((_resolve, reject) => opts.signal.addEventListener('abort', () => reject(new DOMException('aborted','AbortError')))));
  const running = c.action('resume'); await settle(); deadline(); await running;
  assert.equal(c.getSnapshot().busy, false); assert.equal(c.getSnapshot().pending.request.expected_execution_revision, 2); assert.equal(c.getSnapshot().execution.revision, 2); assert.equal(cleared, 1); assert.match(c.getSnapshot().error, /未决预占保留/);
});

// D10 uses synthetic server-owned projections. No supplier requests or price inference.
function responsePolicy(patch = {}) { return { ...policy, wire_api: 'responses', policy_hash: SNAP_B, endpoint: 'https://synthetic.invalid/v1/responses', ...patch }; }
function responseQuote(patch = {}) { return quote({ policy: responsePolicy(), disclosure_hash: '6'.repeat(64), ...patch }); }
function responseGrant(patch = {}) { return grant({ policy_hash: SNAP_B, disclosure_hash: '6'.repeat(64), ...patch }); }
function responseOperation(patch = {}) { return operation({ wire_api: 'responses', policy_hash: SNAP_B, response_status: 'completed', finish_reason: null,
  input_tokens: 100, output_tokens: 40, cached_input_tokens: 30, reasoning_output_tokens: 20, status: 'settled', receipt_status: 'confirmed_local',
  locally_accounted_cny: '0.000090', provider_request_id: 'synthetic-request-1', provider_response_id: 'synthetic-response-1', received_at: WHEN, ...patch }); }

test('D10 reads missing legacy fields as chat/null without mutating original policies or hashes', () => {
  const rawPolicy = Object.freeze({ ...policy }), op = Object.freeze(operation({ finish_reason: 'stop' }));
  const raw = ready({ quotes: [quote({ policy: rawPolicy })], operations: [op] }), before = JSON.stringify(raw);
  const parsed = api.readExecutionDetail(raw);
  assert.equal(parsed.quotes[0].policy.wire_api, 'chat_completions'); assert.equal(parsed.quotes[0].policy.policy_hash, HASH);
  assert.equal(parsed.quotes[0].disclosure_hash, HASH); assert.equal(parsed.operations[0].wire_api, 'chat_completions');
  for (const name of ['response_status','cached_input_tokens','reasoning_output_tokens']) assert.equal(parsed.operations[0][name], null);
  assert.equal(parsed.operations[0].finish_reason, 'stop'); assert.equal(JSON.stringify(raw), before); assert.equal('wire_api' in rawPolicy, false); assert.equal('wire_api' in op, false);
});

test('D10 preserves explicit server protocol, endpoint/model and hashes without rehashing or fallback', () => {
  for (const p of [{ ...policy, wire_api: 'chat_completions' }, responsePolicy()]) {
    const parsed = api.readExecutionPolicy(Object.freeze(p));
    assert.equal(parsed.wire_api, p.wire_api); assert.equal(parsed.endpoint, p.endpoint); assert.equal(parsed.model, p.model); assert.equal(parsed.policy_hash, p.policy_hash);
  }
});

test('D10 rejects unknown/null protocols and mismatched endpoints; missing tag never guesses Responses', () => {
  for (const p of [responsePolicy({ wire_api: null }), responsePolicy({ wire_api: 'automatic' }), responsePolicy({ wire_api: 'chat_completions' }),
    { ...policy, wire_api: 'responses' }, { ...policy, endpoint: responsePolicy().endpoint }, responsePolicy({ endpoint: 'https://synthetic.invalid/v1/responses?key=synthetic' })]) assert.throws(() => api.readExecutionPolicy(p));
  assert.equal(api.readExecutionPolicy({ ...policy, model: 'synthetic-responses-name' }).wire_api, 'chat_completions');
});

test('D10 mixed history keeps each operation protocol independent of the current Responses quote', () => {
  const raw = ready({ quotes: [quote(), responseQuote({ id: Q_NEW })], operations: [operation({ finish_reason: 'stop' }), responseOperation({ id: Q_NEW, quote_id: Q_NEW })] });
  const parsed = api.readExecutionDetail(raw);
  assert.deepEqual(parsed.quotes.map(q => q.policy.wire_api), ['chat_completions','responses']);
  assert.equal(parsed.operations[0].wire_api, 'chat_completions'); assert.equal(parsed.operations[0].response_status, null); assert.equal(parsed.operations[0].finish_reason, 'stop');
  assert.equal(parsed.operations[1].wire_api, 'responses'); assert.equal(parsed.operations[1].response_status, 'completed'); assert.equal(parsed.operations[1].finish_reason, null);
});

test('D10 exposes validated usage subsets without adding/subtracting totals or retaining private reasoning', () => {
  const raw = ready({ quotes: [responseQuote()], grants: [responseGrant()], operations: [responseOperation({ raw: PRIVATE, content: PRIVATE, reasoning_text: PRIVATE, encrypted_content: PRIVATE, annotations: [PRIVATE] })] });
  const parsed = api.readExecutionDetail(raw), op = parsed.operations[0];
  assert.equal(op.input_tokens, 100); assert.equal(op.output_tokens, 40); assert.equal(op.cached_input_tokens, 30); assert.equal(op.reasoning_output_tokens, 20);
  assert.equal(op.locally_accounted_cny, '0.000090'); assert.equal(op.hold_cny, '0.001500'); assert.equal(parsed.ledger.provider_bill_confirmed, false);
  assert.equal(JSON.stringify(parsed).includes(PRIVATE), false);
});

test('D10 optional usage details remain unknown; only an explicit validated zero is displayed as zero', () => {
  for (const patch of [{ cached_input_tokens: undefined, reasoning_output_tokens: undefined }, { cached_input_tokens: null, reasoning_output_tokens: null }]) {
    const parsed = api.readExecutionDetail(ready({ operations: [responseOperation(patch)] })).operations[0];
    assert.equal(parsed.cached_input_tokens, null); assert.equal(parsed.reasoning_output_tokens, null);
  }
  const zero = api.readExecutionDetail(ready({ operations: [responseOperation({ input_tokens: 0, output_tokens: 0, cached_input_tokens: 0, reasoning_output_tokens: 0 })] })).operations[0];
  assert.equal(zero.cached_input_tokens, 0); assert.equal(zero.reasoning_output_tokens, 0);
});

test('D10 rejects invalid subset numbers, parentless details and unknown response statuses', () => {
  for (const patch of [{ cached_input_tokens: true }, { cached_input_tokens: -1 }, { cached_input_tokens: 0.5 }, { cached_input_tokens: 101 },
    { reasoning_output_tokens: 41 }, { reasoning_output_tokens: NaN }, { input_tokens: null }, { output_tokens: null }, { response_status: 'guessed_success' }, { wire_api: null }]) {
    assert.throws(() => api.readExecutionDetail(ready({ operations: [responseOperation(patch)] })));
  }
});

test('D10 never manufactures a chat stop for Responses or a Responses status for legacy chat', () => {
  assert.throws(() => api.readExecutionDetail(ready({ operations: [responseOperation({ finish_reason: 'stop' })] })));
  assert.throws(() => api.readExecutionDetail(ready({ operations: [operation({ response_status: 'completed' })] })));
  const op = api.readExecutionDetail(ready({ operations: [responseOperation({ response_status: undefined })] })).operations[0];
  assert.equal(op.response_status, null); assert.equal(op.finish_reason, null);
});

test('D10 response status is observation only and never clears unknown hold or creates a success result', () => {
  for (const status of ['completed','incomplete','failed','in_progress','queued','cancelled',null]) {
    const ledger = { ...ready().ledger, held_cny: '0.001500', call_count: 1 };
    const parsed = api.readExecutionDetail(ready({ status: 'needs_reconciliation', blocked_reason: 'RECEIPT_INCOMPLETE', allowed_actions: ['stop'], ledger,
      operations: [responseOperation({ status: 'needs_reconciliation', receipt_status: 'unconfirmed', locally_accounted_cny: null, response_status: status })] }));
    assert.equal(parsed.status, 'needs_reconciliation'); assert.equal(parsed.operations[0].response_status, status); assert.equal(parsed.operations[0].locally_accounted_cny, null);
    assert.deepEqual(parsed.ledger, ledger); assert.equal(parsed.plan, null); assert.equal(parsed.quality_status, 'not_reviewed');
  }
});

test('D10 Responses quote/grant/start preserve original disclosure references and never submit client protocol/usage', async t => {
  const s = store(), c = client(s), rq = responseQuote(), rg = responseGrant(); let current = execution(); const posts = [];
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') {
      const input = JSON.parse(opts.body); posts.push({ url, input }); assert.deepEqual(JSON.parse(s.getItem(c.storageKey)).request, input);
      for (const forbidden of ['wire_api','model','endpoint','input_tokens','output_tokens','usage','price','response_status','cached_input_tokens','reasoning_output_tokens']) assert.equal(forbidden in input, false);
      assert.equal(s.getItem(c.storageKey).includes('wire_api'), false);
      if (url.endsWith('/quotes')) { current = execution({ revision: 1, quotes: [rq] }); return result(current, { quote: rq }); }
      if (url.endsWith('/grants')) { assert.equal(input.accepted_disclosure_hash, rq.disclosure_hash); assert.equal(input.quote_id, rq.id); assert.equal(input.expected_execution_revision, 1); current = ready({ quotes: [rq], grants: [rg] }); return result(current, { grant: rg }); }
      assert.equal(input.action, 'resume'); assert.equal(input.expected_execution_revision, 2);
      current = ready({ revision: 3, status: 'queued', allowed_actions: ['stop'], quotes: [rq], grants: [rg] }); return result(current);
    }
    return reads(url, current, policyView({ policy: responsePolicy() }));
  });
  await c.initialize(binding()); await c.requestQuote(); assert.equal(posts.length, 1); assert.equal(c.getSnapshot().reviewed, null);
  c.reviewQuote(true); await c.grant(); assert.equal(posts.length, 2); assert.equal(c.getSnapshot().execution.status, 'ready');
  assert.equal(c.getSnapshot().execution.quotes[0].policy.policy_hash, SNAP_B); await c.action('resume'); assert.equal(posts.length, 3); assert.equal(c.getSnapshot().pending, null);
});

test('D10 malformed new receipt keeps original action key and hold until exact-key confirmation', async t => {
  const c = client(), rq = responseQuote(), rg = responseGrant(), ledger = { ...ready().ledger, held_cny: '0.001500', call_count: 1 };
  let current = ready({ quotes: [rq], grants: [rg], ledger }), original, valid = false;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') {
      const input = JSON.parse(opts.body); if (original) assert.deepEqual(input, original); else original = input;
      current = ready({ revision: 3, status: 'needs_reconciliation', blocked_reason: 'RECEIPT_INCOMPLETE', allowed_actions: ['stop'], quotes: [rq], grants: [rg], ledger,
        operations: [responseOperation({ status: 'needs_reconciliation', receipt_status: 'unconfirmed', locally_accounted_cny: null })] });
      return result(valid ? current : { ...current, operations: [responseOperation({ finish_reason: 'stop' })] }, {}, valid, 3);
    }
    return reads(url, current, policyView({ policy: responsePolicy() }));
  });
  await c.initialize(binding()); await c.action('resume'); assert.ok(c.getSnapshot().pending); assert.equal(c.getSnapshot().execution.ledger.held_cny, '0.001500');
  valid = true; await c.recover(false); assert.ok(c.getSnapshot().pending); assert.equal(c.getSnapshot().execution.status, 'needs_reconciliation');
  await c.recover(true); assert.equal(c.getSnapshot().pending, null); assert.equal(c.getSnapshot().execution.ledger.held_cny, '0.001500');
});
