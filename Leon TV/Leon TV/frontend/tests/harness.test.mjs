import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

// Controlled HTTP at the actual production controller/adapter seam. These tests
// do not stand in for the separately coordinated real API/persistence/UI checks.
const { module: control } = await loadModule('lib/harnessRunController.ts');
const { module: api } = await loadModule('lib/harnessApi.ts');
const PROJECT = 'test-project';
const RUN_ID = 'a'.repeat(32);
const KEY = 'test-client-request-0001';
const HASH = 'a'.repeat(64);
function run(overrides = {}) {
  return {
    schema_version: 1, id: RUN_ID, mode: 'test', test_mode: true, evidence_type: 'fixture',
    project_id: PROJECT, client_request_key: KEY, scenario: 'success', status: 'queued', revision: 1,
    snapshot_sequence: 1, source: { hash: HASH, record_ids: ['studio-test-project'] }, production: { ratio: '9:16', duration_ms: 15000 },
    blocked_reason: null, plan: [{ id: 'reference', depends_on: [] }, { id: 'shot', depends_on: ['reference'] }, { id: 'final', depends_on: ['shot'] }],
    artifacts: [], reviews: [], manifest: null,
    budget: { currency: 'CNY', paid_limit: '0.00', paid_reserved: '0.00', paid_actual: '0.00', fixture_limit: 8, fixture_used: 0, unresolved_operations: 0 },
    metrics: { model_http_calls: 0, paid_calls: 0, repair_count: 0, final_decision_count: 0 }, final_review: null,
    allowed_actions: ['stop', 'revise'], created_at: '2026-10-19T00:00:00Z', updated_at: '2026-10-19T00:00:00Z', ...overrides,
  };
}
function reviewableRun() {
  const artifacts = ['reference', 'shot', 'final'].map((id, index) => ({
    id, version: 1, kind: 'video', mode: 'test', generation_status: 'succeeded', quality_status: 'accepted',
    required_scopes: ['fixture_integrity', 'fixture_content'],
    dependencies: index ? [{ artifact_id: ['reference', 'shot'][index - 1], version: 1, sha256: String(index).repeat(64) }] : [],
    sha256: String(index + 1).repeat(64), media_url: `/api/harness/runs/${RUN_ID}/artifacts/${id}/media?version=1`, review_ids: [`review-${id}`], stale: false,
  }));
  return run({ status: 'waiting_human_review', revision: 12, artifacts,
    reviews: artifacts.map(artifact => ({ id: `review-${artifact.id}`, artifact_id: artifact.id, artifact_version: 1,
      artifact_sha256: artifact.sha256, mode: 'test', evidence_type: 'fixture', status: 'accepted',
      scopes: { fixture_integrity: 'accepted', fixture_content: 'accepted' }, rule_version: 'fixture-v1', valid: true, reason: null, receipt_id: 'logical-receipt' })),
    manifest: { version: 1, final_artifact_id: 'final', final_version: 1, final_sha256: artifacts[2].sha256, review_bundle_hash: HASH, valid: true },
  });
}
function store(initial = {}) {
  const values = new Map(Object.entries(initial));
  return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
}
function controller(storage) { return new control.HarnessRunController(PROJECT, () => storage); }
function project(nodes = []) { return { id: PROJECT, title: 'Synthetic test project', revision: 1, nodes, edges: [] }; }
function notFound() { return Response.json({ detail: { code: 'NOT_FOUND', message: 'No test run' } }, { status: 404 }); }
function savedCreation(storage, snapshot) {
  const request = { mode: 'test', client_request_key: KEY, project_id: PROJECT, source: { hash: HASH, record_ids: ['studio-test-project'] }, production: { ratio: '9:16', duration_ms: 15000 }, authorization_ref: 'test-zero-paid-v1', scenario: 'success' };
  storage.setItem(controller(storage).storageKey, JSON.stringify({ version: 1, project_id: PROJECT, client_request_key: KEY, create_request: request, last_snapshot: snapshot }));
  return request;
}

test('empty project creation persists its exact key before POST and uses valid stable record IDs', async t => {
  const storage = store();
  const client = controller(storage);
  let submitted;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url === `/api/studio/projects/${PROJECT}`) return Response.json(project());
    assert.equal(url, '/api/harness/runs');
    submitted = JSON.parse(options.body);
    assert.deepEqual(client.checkpoint().create_request, submitted, 'durable intent must precede dispatch');
    return Response.json({ run: run({ client_request_key: submitted.client_request_key }) }, { status: 201 });
  });
  await client.start('success');
  assert.equal(submitted.mode, 'test');
  assert.deepEqual(submitted.production, { ratio: '9:16', duration_ms: 15000 });
  assert.match(submitted.source.hash, /^[a-f0-9]{64}$/);
  assert.ok(submitted.source.record_ids.every(id => /^[A-Za-z0-9_.-]{1,128}$/.test(id)));
  assert.equal(client.checkpoint().run_id, RUN_ID);
});

test('arbitrary node IDs and private synthetic text are hashed locally rather than sent as source records', async t => {
  let submitted;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url.startsWith('/api/studio/')) return Response.json(project([{ id: 'node:with:colon/中文', data: { description: 'private synthetic text' } }]));
    submitted = JSON.parse(options.body);
    return Response.json({ run: run({ client_request_key: submitted.client_request_key }) });
  });
  await controller(store()).start('repair');
  assert.ok(submitted.source.record_ids.every(id => /^[A-Za-z0-9_.-]{1,128}$/.test(id)));
  assert.equal(JSON.stringify(submitted).includes('private synthetic text'), false);
  assert.equal(JSON.stringify(submitted).includes('node:with'), false);
});

test('lost creation response is recovered by the original key after remount with no second POST', async t => {
  const storage = store();
  let submitted;
  let posts = 0;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url.startsWith('/api/studio/')) return Response.json(project());
    if (options?.method === 'POST') { posts++; submitted = JSON.parse(options.body); throw new TypeError('response lost'); }
    assert.equal(url, `/api/harness/runs/by-key/${submitted.client_request_key}`);
    return Response.json({ run: run({ client_request_key: submitted.client_request_key }) });
  });
  await assert.rejects(controller(storage).start('success'), /response lost/);
  const key = controller(storage).checkpoint().client_request_key;
  const restored = await controller(storage).restore();
  assert.equal(restored.client_request_key, key);
  assert.equal(posts, 1);
  assert.equal(controller(storage).checkpoint().run_id, RUN_ID);
});

test('by-key 404, 503 and timeout preserve the original key and old snapshot without creation', async t => {
  for (const failure of [404, 503, 'timeout']) {
    const storage = store();
    const old = run({ id: 'b'.repeat(32), client_request_key: 'old-client-key-0001', revision: 14 });
    savedCreation(storage, old);
    const before = storage.getItem(controller(storage).storageKey);
    t.mock.method(globalThis, 'fetch', async (_url, options) => {
      assert.notEqual(options?.method, 'POST');
      if (failure === 'timeout') throw new TypeError('timeout');
      return failure === 404 ? notFound() : Response.json({ detail: { code: 'PRIVATE_STORAGE_UNAVAILABLE', message: 'temporarily unavailable' } }, { status: 503 });
    });
    if (failure === 404) assert.equal(await controller(storage).restore(), null);
    else await assert.rejects(controller(storage).restore());
    assert.equal(storage.getItem(controller(storage).storageKey), before);
    await assert.rejects(controller(storage).start('repair'), /尚未确认/);
  }
});

test('an explicit retry queries first and sends the stored body and key unchanged only after 404', async t => {
  const storage = store();
  const original = savedCreation(storage);
  const methods = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    methods.push(options?.method || 'GET');
    if (url.includes('/by-key/')) return notFound();
    assert.deepEqual(JSON.parse(options.body), original);
    return Response.json({ run: run() });
  });
  await controller(storage).retryCreation();
  assert.deepEqual(methods, ['GET', 'POST']);
});

test('duplicate clicks cannot dispatch two creates while the first click awaits the project', async t => {
  const storage = store();
  const client = controller(storage);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let posts = 0;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url.startsWith('/api/studio/')) { await gate; return Response.json(project()); }
    posts++;
    return Response.json({ run: run({ client_request_key: JSON.parse(options.body).client_request_key }) });
  });
  const first = client.start('success');
  await assert.rejects(client.start('success'), /上一操作/);
  release(); await first;
  assert.equal(posts, 1);
});

test('a second controller sharing project storage cannot replace a create intent during source hashing', async t => {
  const storage = store();
  let posts = 0;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url.startsWith('/api/studio/')) return Response.json(project());
    posts++;
    return Response.json({ run: run({ client_request_key: JSON.parse(options.body).client_request_key }) });
  });
  const results = await Promise.allSettled([controller(storage).start('success'), controller(storage).start('success')]);
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
  assert.equal(posts, 1);
});

test('unavailable durable storage prevents every creation POST', async t => {
  let posts = 0;
  t.mock.method(globalThis, 'fetch', async (_url, options) => { if (options?.method === 'POST') posts++; return Response.json(project()); });
  await assert.rejects(controller({ getItem: () => null, setItem: () => { throw new Error('quota'); } }).start('success'), /持久保存/);
  assert.equal(posts, 0);
});

test('real D06 scope maps permit current fixture review and unknown/rejected/stale evidence blocks it', () => {
  assert.equal(control.canReviewHarness(reviewableRun()), true);
  for (const change of [
    value => { value.reviews[2].scopes.fixture_content = 'unknown'; },
    value => { value.reviews[0].valid = false; },
    value => { value.artifacts[1].quality_status = 'rejected'; },
    value => { value.artifacts[0].stale = true; },
    value => { value.manifest.final_sha256 = HASH; },
    value => { value.manifest.valid = false; },
    value => { value.budget.unresolved_operations = 1; },
  ]) { const value = reviewableRun(); change(value); assert.equal(control.canReviewHarness(value), false); }
});

test('test review binds revision, manifest and final hash before dispatch and never auto-retries a conflict', async t => {
  const storage = store();
  const client = controller(storage);
  const current = reviewableRun();
  client.track(current);
  let posts = 0;
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    posts++;
    const request = JSON.parse(options.body);
    assert.deepEqual(client.checkpoint().pending.request, request);
    assert.equal(request.mode, 'test'); assert.equal(request.expected_revision, 12);
    assert.equal(request.manifest_version, 1); assert.equal(request.final_sha256, current.manifest.final_sha256);
    return Response.json({ detail: { code: 'VERSION_CONFLICT', message: 'Changed version' } }, { status: 409 });
  });
  await assert.rejects(client.review(current, 'accept', ''), error => error instanceof api.HarnessApiError && error.code === 'VERSION_CONFLICT');
  assert.equal(posts, 1);
  assert.equal(client.checkpoint().pending, undefined);
  assert.equal(client.checkpoint().last_snapshot.revision, 12);
});

test('lost action response survives remount; an explicit retry reuses the exact action key after a GET', async t => {
  const storage = store();
  const current = run({ status: 'stopped', allowed_actions: ['resume', 'stop'] });
  controller(storage).track(current);
  let original;
  let loseResponse = true;
  const methods = [];
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    methods.push(options?.method || 'GET');
    if (options?.method !== 'POST') return Response.json({ run: current });
    const request = JSON.parse(options.body);
    if (loseResponse) { original = request; throw new TypeError('response lost'); }
    assert.deepEqual(request, original);
    return Response.json({ run: run({ status: 'running', revision: 2 }) });
  });
  await assert.rejects(controller(storage).act(current, 'resume'), /response lost/);
  await controller(storage).restore();
  assert.deepEqual(methods, ['POST', 'GET']);
  assert.deepEqual(controller(storage).checkpoint().pending.request, original);
  loseResponse = false;
  await controller(storage).retryPending();
  assert.deepEqual(methods, ['POST', 'GET', 'GET', 'POST']);
  assert.equal(controller(storage).checkpoint().pending, undefined);
});

test('lost test-review response retains the exact version binding and review key through remount', async t => {
  const storage = store();
  const current = reviewableRun();
  controller(storage).track(current);
  let original;
  let loseResponse = true;
  let posts = 0;
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    if (options?.method !== 'POST') return Response.json({ run: current });
    posts++;
    const request = JSON.parse(options.body);
    if (loseResponse) { original = request; throw new TypeError('review response lost'); }
    assert.deepEqual(request, original);
    return Response.json({ run: run({ ...current, revision: 13, status: 'test_accepted', final_review: { test_mode: true } }) });
  });
  await assert.rejects(controller(storage).review(current, 'accept', 'test note'), /review response lost/);
  await controller(storage).restore();
  assert.equal(posts, 1);
  assert.deepEqual(controller(storage).checkpoint().pending.request, original);
  loseResponse = false;
  assert.equal((await controller(storage).retryPending()).status, 'test_accepted');
  assert.equal(posts, 2);
  assert.equal(controller(storage).checkpoint().pending, undefined);
});

test('unresolved external outcomes forbid revise/resume even if an old allowed_actions list includes them', async t => {
  const storage = store();
  const current = run({ status: 'needs_reconciliation', allowed_actions: ['revise', 'resume', 'reconcile'], budget: { ...run().budget, unresolved_operations: 1 } });
  const client = controller(storage); client.track(current);
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => { calls++; throw new Error('must not dispatch'); });
  await assert.rejects(client.act(current, 'revise', 'reference'), /先对账/);
  await assert.rejects(client.act(current, 'resume'), /先对账/);
  assert.equal(calls, 0);
});

test('an old slow restore cannot overwrite the checkpoint for a newly selected run', async t => {
  const storage = store();
  const client = controller(storage);
  client.track(run());
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  t.mock.method(globalThis, 'fetch', async () => { await gate; return Response.json({ run: run({ revision: 2 }) }); });
  const restoring = client.restore();
  const newer = run({ id: 'b'.repeat(32), client_request_key: 'new-client-key-0001', revision: 20 });
  client.track(newer);
  release(); await restoring;
  assert.equal(client.checkpoint().run_id, newer.id);
  assert.equal(client.checkpoint().last_snapshot.revision, 20);
});

test('view guard rejects old selection, older poll revision, and responses after project switch/unmount', () => {
  const guard = new control.HarnessViewGuard();
  const old = guard.begin('run-old');
  assert.equal(guard.accept(old, { id: 'run-old', revision: 8 }), true);
  assert.equal(guard.accept(old, { id: 'run-old', revision: 7 }), false);
  const current = guard.begin('run-new');
  assert.equal(guard.accept(old, { id: 'run-old', revision: 99 }), false);
  assert.equal(guard.accept(current, { id: 'run-old', revision: 99 }), false);
  assert.equal(guard.accept(current, { id: 'run-new', revision: 1 }), true);
  guard.invalidate();
  assert.equal(guard.accept(current, { id: 'run-new', revision: 99 }), false);
});

test('poll snapshots update history status and an older list response cannot undo them', () => {
  const queued = run();
  const waiting = reviewableRun();
  const updated = control.mergeHarnessRuns([queued], [waiting]);
  assert.equal(updated[0].status, 'waiting_human_review');
  assert.equal(control.mergeHarnessRuns(updated, [queued])[0].revision, waiting.revision);
});

test('controller refuses a response belonging to a different project without changing saved intent', async t => {
  const storage = store(); savedCreation(storage);
  const before = storage.getItem(controller(storage).storageKey);
  t.mock.method(globalThis, 'fetch', async () => Response.json({ run: run({ project_id: 'another-project' }) }));
  await assert.rejects(controller(storage).restore(), /项目不匹配/);
  assert.equal(storage.getItem(controller(storage).storageKey), before);
});

test('adapter reads real scope-map shape, rejects a non-test run and malformed scope arrays', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ run: reviewableRun() }));
  assert.equal(control.canReviewHarness(await api.harnessApi.get(RUN_ID)), true);
  t.mock.method(globalThis, 'fetch', async () => Response.json({ run: run({ mode: 'production' }) }));
  await assert.rejects(api.harnessApi.get(RUN_ID), /测试合同/);
  const invalid = reviewableRun(); invalid.reviews[0].scopes = ['fixture_integrity'];
  t.mock.method(globalThis, 'fetch', async () => Response.json({ run: invalid }));
  await assert.rejects(api.harnessApi.get(RUN_ID), /格式错误/);
});

test('events are read by cursor and reject events from another run; media uses only run/artifact/version', async t => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, `/api/harness/runs/${RUN_ID}/events?after_sequence=8&limit=100`);
    assert.notEqual(options?.method, 'POST');
    return Response.json({ mode: 'test', events: [{ mode: 'test', run_id: RUN_ID, sequence: 9, type: 'reviewed', timestamp: '2026-10-19T00:00:00Z' }], next_sequence: 9, has_more: false });
  });
  assert.equal((await api.harnessApi.events(RUN_ID, 8)).next_sequence, 9);
  t.mock.method(globalThis, 'fetch', async () => Response.json({ mode: 'test', events: [{ mode: 'test', run_id: 'wrong-run', sequence: 9, type: 'reviewed' }], next_sequence: 9, has_more: false }));
  await assert.rejects(api.harnessApi.events(RUN_ID), /标识错误/);
  assert.equal(api.harnessMediaUrl(RUN_ID, 'final', 2), `/api/harness/runs/${RUN_ID}/artifacts/final/media?version=2`);
});
