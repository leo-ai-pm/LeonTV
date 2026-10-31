import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: api } = await loadModule('lib/plannerMediaApi.ts');
const { module: control } = await loadModule('lib/plannerMediaController.ts');
const { module: view } = await loadModule('lib/plannerMediaView.ts');
const A = 'a'.repeat(32), B = 'b'.repeat(32), HASH = 'c'.repeat(64), OTHER = 'd'.repeat(64);
const binding = (task = A, snapshot = HASH) => ({ task_id: task, source_snapshot_hash: snapshot });
const clone = value => structuredClone(value);
function fixture(task = A, snapshot = HASH) {
  return {
    media: { retained_exposure_cny: '83.370000', platform_observed_cny: null, provider_bill_confirmed: false, risk_released: false,
      operations: [{ delivered: true }], allowed_actions: [] },
    production_images: { schema_version: 1, task_id: task, source_snapshot_hash: snapshot, evidence_kind: 'synthetic', scope_id: '1'.repeat(32),
      status: 'observed', read_only: true, required_count: 1, registered_count: 1, verified_file_count: 1, needs_input_count: 0,
      shots: [{ shot_id: 'S01', first_frame_version: 1, operation_id: '2'.repeat(32), query_id: '3'.repeat(32), opt_in: true,
        generation: 'accepted', completion: 'completed', reception: 'saved', received: true, persisted: true, delivery_registered: true,
        delivery_origin: 'managed', historical_saved: true, file_availability: 'available', authority: 'valid', fee_observed: false,
        http_status: 200, error_code: null, needs_input: false, retry_authorized: false, quality: 'not_reviewed', human_acceptance: 'pending' }] },
  };
}
function variant(patch) {
  const data = fixture(), p = data.production_images;
  Object.assign(p.shots[0], patch);
  p.registered_count = Number(p.shots[0].delivery_registered);
  p.verified_file_count = Number(p.shots[0].file_availability === 'available');
  p.needs_input_count = Number(p.shots[0].needs_input);
  p.status = p.needs_input_count ? 'attention' : 'observed';
  return data;
}
const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
async function withFetch(fetcher, run) {
  const original = globalThis.fetch;
  globalThis.fetch = fetcher;
  try { await run(); } finally { globalThis.fetch = original; }
}
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const shot = data => data.production_images.shots[0];

test('D48 decoder binds task and frozen source and preserves unknown fees and original risk', () => {
  const data = api.readPlannerMedia(fixture(), binding());
  assert.equal(data.production_images.source_snapshot_hash, HASH);
  assert.equal(data.media.platform_observed_cny, null);
  assert.equal(data.media.retained_exposure_cny, '83.370000');
  assert.equal(shot(data).fee_observed, false);
  assert.deepEqual(view.mediaShotPresentation(shot(data), true), { label: '已保存', detail: '图片文件本次核验可用，已完成正式登记。', tone: 'saved' });
});

test('D48 rejects wrong task/source/schema/evidence and all missing required shot fields', () => {
  for (const modify of [d => { d.production_images.task_id = B; }, d => { d.production_images.source_snapshot_hash = OTHER; },
    d => { delete d.production_images.source_snapshot_hash; }, d => { delete d.production_images.evidence_kind; },
    d => { d.production_images.evidence_kind = 'live'; }, d => { d.production_images.schema_version = 2; },
    d => { delete d.production_images; }, d => { d.production_images.read_only = false; }]) {
    const data = fixture(); modify(data);
    assert.throws(() => api.readPlannerMedia(data, binding()), { code: 'INVALID_RESPONSE' });
  }
  for (const key of Object.keys(shot(fixture()))) {
    const data = fixture(); delete shot(data)[key];
    assert.throws(() => api.readPlannerMedia(data, binding()), { code: 'INVALID_RESPONSE' }, key);
  }
});

test('D48 rejects invalid enum/boolean/identity/version/status/count and contradictory success', () => {
  const patches = [ { generation: 'success' }, { completion: 'SUCCESS' }, { reception: 'delivered' }, { file_availability: 'ready' },
    { authority: 'enabled' }, { delivery_origin: 'diagnostic' }, { error_code: 'RAW_SECRET_ERROR' }, { opt_in: 'true' },
    { received: 1 }, { persisted: undefined }, { delivery_registered: 'true' }, { fee_observed: 0 }, { needs_input: null },
    { first_frame_version: 0 }, { operation_id: B + '/secret' }, { query_id: 'x' }, { http_status: 700 }, { http_status: '404' },
    { retry_authorized: true }, { quality: 'accepted' }, { human_acceptance: 'accepted' }, { shot_id: 'https://private.invalid/signed' },
    { persisted: true, received: false }, { delivery_registered: false }, { generation: 'reserved' }, { file_availability: 'missing' },
  ];
  for (const patch of patches) { const d = fixture(); Object.assign(shot(d), patch); assert.throws(() => api.readPlannerMedia(d, binding()), { code: 'INVALID_RESPONSE' }, JSON.stringify(patch)); }
  for (const key of ['required_count', 'registered_count', 'verified_file_count', 'needs_input_count']) {
    const d = fixture(); d.production_images[key] = 9;
    assert.throws(() => api.readPlannerMedia(d, binding()), { code: 'INVALID_RESPONSE' }, key);
  }
  const d = fixture(); d.production_images.shots.push(clone(shot(d))); d.production_images.required_count = 2;
  assert.throws(() => api.readPlannerMedia(d, binding()), { code: 'INVALID_RESPONSE' });
});

test('D48 drops private extras at envelope/media/production/shot levels without mutating input', () => {
  const d = fixture(), secret = 'PRIVATE_RAW_KEY_URL_PATH_SENTINEL';
  d.raw = secret; d.media.raw = secret; d.media.operations[0].url = secret; d.production_images.path = secret; shot(d).url = secret;
  const before = clone(d), decoded = api.readPlannerMedia(d, binding());
  assert.equal(JSON.stringify(decoded).includes(secret), false);
  assert.equal('operations' in decoded.media, false);
  assert.equal('url' in shot(decoded), false);
  assert.deepEqual(d, before);
});

test('D48 no-scope empty requires null evidence and exact source; legacy delivered never proves current bytes', () => {
  const d = fixture(); Object.assign(d.production_images, { scope_id: null, evidence_kind: null, status: 'not_configured', required_count: 0, registered_count: 0, verified_file_count: 0, needs_input_count: 0, shots: [] });
  assert.equal(api.readPlannerMedia(d, binding()).production_images.shots.length, 0);
  const bad = clone(d); bad.production_images.evidence_kind = 'synthetic';
  assert.throws(() => api.readPlannerMedia(bad, binding()), { code: 'INVALID_RESPONSE' });
  const legacy = variant({ opt_in: false, reception: 'not_enabled', received: null, persisted: null, delivery_origin: 'legacy', historical_saved: false,
    file_availability: 'not_verified', authority: 'not_applicable', error_code: 'LEGACY_FILE_NOT_VERIFIED', needs_input: true, http_status: null });
  const decoded = api.readPlannerMedia(legacy, binding());
  assert.equal(shot(decoded).delivery_registered, true);
  assert.equal(view.mediaShotPresentation(shot(decoded), true).label, '结果待确认');
  assert.equal(shot(decoded).received, null);
});

test('D48 explicitly distinguishes synthetic and empirical receipt sources', () => {
  const d = fixture(); d.production_images.evidence_kind = 'empirical_receipts';
  assert.equal(api.readPlannerMedia(d, binding()).production_images.evidence_kind, 'empirical_receipts');
  d.production_images.evidence_kind = null;
  assert.throws(() => api.readPlannerMedia(d, binding()), { code: 'INVALID_RESPONSE' });
});

test('D48 saved but unregistered and unpublished proof remain incomplete', () => {
  const unregistered = api.readPlannerMedia(variant({ delivery_registered: false, delivery_origin: 'none', needs_input: true, error_code: 'DELIVERY_PENDING' }), binding());
  assert.equal(view.mediaShotPresentation(shot(unregistered), true).label, '结果待确认');
  assert.match(view.mediaShotPresentation(shot(unregistered), true).detail, /登记尚未完成/);
  const unpublished = api.readPlannerMedia(variant({ received: true, persisted: false, reception: 'receiving', file_availability: 'not_published',
    historical_saved: false, delivery_registered: false, delivery_origin: 'none', needs_input: true, error_code: 'PERSISTENCE_PENDING' }), binding());
  assert.equal(view.mediaShotPresentation(shot(unpublished), true).label, '已生成待保存');
});

test('D48 published bytes before native saved commit are a valid partial, not a schema failure', () => {
  const partial = api.readPlannerMedia(variant({ reception: 'receiving', historical_saved: false, delivery_registered: false,
    delivery_origin: 'none', needs_input: true, error_code: 'DELIVERY_PENDING' }), binding());
  assert.equal(shot(partial).file_availability, 'available');
  assert.equal(shot(partial).reception, 'receiving');
  assert.equal(view.mediaShotPresentation(shot(partial), true).label, '结果待确认');
  const pointerGap = api.readPlannerMedia(variant({ reception: 'receiving', historical_saved: false }), binding());
  assert.equal(view.mediaShotPresentation(shot(pointerGap), true).label, '结果待确认');
});

test('D48 available or managed registration requires exact operation, query and completed generation', () => {
  for (const base of [fixture(), variant({ received: false, persisted: false, file_availability: 'missing', needs_input: true, error_code: 'FILE_MISSING' })]) {
    for (const patch of [{ operation_id: null }, { query_id: null }, { completion: 'not_observed' }, { completion: 'pending' }]) {
      const data = clone(base); Object.assign(shot(data), patch);
      assert.throws(() => api.readPlannerMedia(data, binding()), { code: 'INVALID_RESPONSE' });
    }
  }
});

test('D48 resource404/unknown do not erase completed or invent URL expiry', () => {
  for (const [reception, code, label] of [['failed', 'HTTP_NOT_200', '保存失败'], ['unknown', 'TIMEOUT', '结果待确认']]) {
    const d = api.readPlannerMedia(variant({ reception, received: false, persisted: false, historical_saved: false, delivery_registered: false, delivery_origin: 'none',
      file_availability: 'not_recorded', needs_input: true, http_status: reception === 'failed' ? 404 : null, error_code: code }), binding());
    const presentation = view.mediaShotPresentation(shot(d), true);
    assert.equal(shot(d).completion, 'completed'); assert.equal(presentation.label, label); assert.doesNotMatch(presentation.detail, /过期/);
  }
});

test('D48 file loss/corruption keeps historical registration; expiry alone does not erase valid bytes', () => {
  for (const [file, code] of [['missing', 'FILE_MISSING'], ['invalid', 'FILE_INVALID'], ['not_verified', 'AUTHORITY_UNAVAILABLE']]) {
    const d = api.readPlannerMedia(variant({ file_availability: file, received: file === 'not_verified' ? null : false,
      persisted: file === 'not_verified' ? null : false, authority: 'expired', needs_input: true, error_code: code }), binding());
    assert.equal(shot(d).delivery_registered, true); assert.equal(view.mediaShotPresentation(shot(d), true).label, '结果待确认');
  }
  const d = api.readPlannerMedia(variant({ authority: 'expired' }), binding());
  assert.equal(view.mediaShotPresentation(shot(d), true).label, '已保存');
});

test('D48 pending/reserved/failure views describe only observed generation facts', () => {
  const d = fixture();
  for (const [generation, completion, expected] of [['not_started', 'not_observed', '尚未生成'], ['reserved', 'not_observed', '等待生成'],
    ['submitting', 'not_observed', '生成中'], ['accepted', 'pending', '生成中'], ['rejected', 'not_observed', '生成失败'], ['accepted', 'failed', '生成失败']]) {
    Object.assign(shot(d), { generation, completion, reception: 'not_started', persisted: false, received: false, file_availability: 'not_recorded', delivery_origin: 'none', delivery_registered: false, historical_saved: false });
    Object.assign(d.production_images, { registered_count: 0, verified_file_count: 0 });
    assert.equal(view.mediaShotPresentation(shot(api.readPlannerMedia(d, binding())), true).label, expected);
  }
});

test('D48 stale views always withdraw success even when old byte metadata said available', () => {
  for (const d of [fixture(), variant({ authority: 'expired' }), variant({ needs_input: true, error_code: 'DELIVERY_PENDING', delivery_registered: false, delivery_origin: 'none' })]) {
    const presentation = view.mediaShotPresentation(shot(api.readPlannerMedia(d, binding())), false);
    assert.equal(presentation.label, '结果待确认'); assert.equal(presentation.tone, 'neutral'); assert.match(presentation.detail, /上次读取/);
  }
});

test('D48 adapter only uses one local GET with no body, cache, redirect or external image access', async () => {
  const requests = [];
  await withFetch(async (url, init) => { requests.push({ url, init }); return response(fixture()); }, async () => {
    await api.plannerMediaApi.get(binding());
    assert.equal(requests.length, 1); assert.equal(requests[0].url, `/api/planner/tasks/${A}/execution/media`);
    assert.equal(requests[0].init.method, 'GET'); assert.equal(requests[0].init.body, undefined);
    assert.equal(requests[0].init.cache, 'no-store'); assert.equal(requests[0].init.redirect, 'error');
  });
});

test('D48 only precise execution NOT_FOUND404 is empty; generic404/503/HTML/schema errors are failures', async () => {
  await withFetch(async () => response({ detail: { code: 'NOT_FOUND', message: 'Execution record not found' } }, 404), async () => {
    assert.equal(await api.plannerMediaApi.get(binding()), null);
  });
  for (const [body, status] of [[{ detail: { code: 'OTHER', message: 'PRIVATE_ERROR' } }, 404], [{ detail: { code: 'NOT_FOUND', message: 'Not this execution' } }, 404],
    [{ detail: { code: 'NOT_FOUND', message: 'Execution record not found' } }, 503], [fixture(), 503], [{ media: { delivered: true } }, 200]]) {
    await withFetch(async () => response(body, status), async () => {
      await assert.rejects(api.plannerMediaApi.get(binding()), error => error instanceof api.PlannerMediaError && !error.message.includes('PRIVATE_ERROR'));
    });
  }
  await withFetch(async () => new Response('<html>PRIVATE_ERROR</html>', { status: 500 }), async () => {
    await assert.rejects(api.plannerMediaApi.get(binding()), error => error.code === 'READ_FAILED' && !error.message.includes('PRIVATE_ERROR'));
  });
});

test('D48 invalid requested identity rejects before any fetch', async () => {
  let calls = 0;
  await withFetch(async () => { calls++; return response(fixture()); }, async () => {
    await assert.rejects(api.plannerMediaApi.get(binding('../unsafe')), { code: 'INVALID_RESPONSE' });
    await assert.rejects(api.plannerMediaApi.get(binding(A, 'unsafe')), { code: 'INVALID_RESPONSE' });
    assert.equal(calls, 0);
  });
});

test('D48 controller initial error is not empty and refresh can recover without POST', async () => {
  const c = new control.PlannerMediaController(), requests = []; let succeed = false;
  await withFetch(async (url, init) => { requests.push(init.method); if (!succeed) throw new Error('PRIVATE_NETWORK'); return response(fixture()); }, async () => {
    await c.select(binding()); assert.equal(c.getSnapshot().phase, 'error'); assert.equal(c.getSnapshot().data, null);
    assert.equal(c.getSnapshot().error.includes('PRIVATE_NETWORK'), false);
    succeed = true; await c.refresh(); assert.equal(c.getSnapshot().phase, 'ready');
    assert.deepEqual(requests, ['GET', 'GET']); c.invalidate();
  });
});

test('D48 refresh immediately withdraws verification; failed refresh keeps marked old data', async () => {
  const c = new control.PlannerMediaController(), late = deferred(); let reads = 0;
  await withFetch(async () => ++reads === 1 ? response(fixture()) : late.promise, async () => {
    await c.select(binding()); const before = c.getSnapshot();
    const refresh = c.refresh(); assert.equal(c.getSnapshot().phase, 'refreshing');
    assert.equal(view.mediaShotPresentation(shot(c.getSnapshot().data), c.getSnapshot().phase === 'ready').tone, 'neutral');
    late.resolve(response({ detail: { code: 'SERVICE_UNAVAILABLE' } }, 503)); await refresh;
    assert.equal(c.getSnapshot().phase, 'error'); assert.equal(c.getSnapshot().data, before.data); assert.equal(c.getSnapshot().observedAt, before.observedAt);
    c.invalidate();
  });
});

test('D48 switches A→B immediately, ignores late A even while its JSON decode was pending', async () => {
  const c = new control.PlannerMediaController(), jsonA = deferred(); let reads = 0;
  await withFetch(async () => ++reads === 1 ? { ok: true, status: 200, json: () => jsonA.promise } : response(fixture(B)), async () => {
    const a = c.select(binding()); await Promise.resolve();
    const b = c.select(binding(B)); assert.equal(c.getSnapshot().data, null); assert.equal(c.getSnapshot().binding.task_id, B);
    await b; jsonA.resolve(fixture()); await a;
    assert.equal(c.getSnapshot().phase, 'ready'); assert.equal(c.getSnapshot().data.production_images.task_id, B);
    c.invalidate();
  });
});

test('D48 same task changed snapshot clears old data and ignores old response', async () => {
  const c = new control.PlannerMediaController(), late = deferred(); let reads = 0;
  await withFetch(async () => ++reads === 1 ? late.promise : response(fixture(A, OTHER)), async () => {
    const old = c.select(binding()); await c.select(binding(A, OTHER));
    late.resolve(response(fixture())); await old;
    assert.equal(c.getSnapshot().data.production_images.source_snapshot_hash, OTHER); c.invalidate();
  });
});

test('D48 refresh/duplicate selection during a read is deduplicated and cannot strand loading', async () => {
  const c = new control.PlannerMediaController(), late = deferred(); let reads = 0;
  await withFetch(async () => { reads++; return late.promise; }, async () => {
    const first = c.select(binding()); await c.refresh(); await c.select(binding()); await c.refresh();
    assert.equal(reads, 1); late.resolve(response(fixture())); await first;
    assert.equal(c.getSnapshot().phase, 'ready'); c.invalidate();
  });
});

test('D48 old refresh cannot overwrite a new task and malformed response cannot replace verified data', async () => {
  const c = new control.PlannerMediaController(), late = deferred(); let reads = 0;
  await withFetch(async () => { reads++; return reads === 2 ? late.promise : response(fixture(reads >= 3 ? B : A)); }, async () => {
    await c.select(binding()); const oldRefresh = c.refresh(); await c.select(binding(B)); late.resolve(response(fixture())); await oldRefresh;
    assert.equal(c.getSnapshot().data.production_images.task_id, B); c.invalidate();
  });
  await withFetch(async () => response(fixture()), async () => { await c.select(binding()); });
  const before = c.getSnapshot().data;
  await withFetch(async () => response(fixture(B)), async () => { await c.refresh(); });
  assert.equal(c.getSnapshot().phase, 'error'); assert.equal(c.getSnapshot().data, before); c.invalidate();
});

test('D48 unmount invalidates a pending response and allows strict-mode reselect', async () => {
  const c = new control.PlannerMediaController(), late = deferred(); let reads = 0;
  await withFetch(async () => ++reads === 1 ? late.promise : response(fixture()), async () => {
    const pending = c.select(binding()); c.invalidate(); const afterUnmount = c.getSnapshot();
    late.resolve(response(fixture())); await pending; assert.equal(c.getSnapshot(), afterUnmount);
    await c.select(binding()); assert.equal(c.getSnapshot().phase, 'ready'); c.invalidate();
  });
});

test('D48 precise missing execution transitions to empty and never writes or auto retries', async () => {
  const c = new control.PlannerMediaController(), requests = [];
  await withFetch(async (url, init) => { requests.push(init.method); return response({ detail: { code: 'NOT_FOUND', message: 'Execution record not found' } }, 404); }, async () => {
    await c.select(binding()); assert.equal(c.getSnapshot().phase, 'not_found'); assert.equal(c.getSnapshot().data, null);
    await c.select(binding()); assert.equal(requests.length, 1);
    await c.refresh(); assert.deepEqual(requests, ['GET', 'GET']); c.invalidate();
  });
});
