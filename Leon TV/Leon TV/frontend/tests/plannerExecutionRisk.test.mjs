import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: api } = await loadModule('lib/plannerExecutionApi.ts');
const { module: types } = await loadModule('lib/plannerExecutionTypes.ts');
const { module: roles } = await loadModule('lib/plannerExecutionRoles.ts');
const { module: control } = await loadModule('lib/plannerExecutionController.ts');
const { module: view } = await loadModule('lib/plannerExecutionView.ts');
const { module: risk } = await loadModule('lib/plannerExecutionRisk.ts');
const A = 'a'.repeat(32), Q = '1'.repeat(32), G = '2'.repeat(32), OP = '3'.repeat(32), HASH = 'e'.repeat(64), SNAP = 'd'.repeat(64), ANCHOR = 'f'.repeat(64);
const WHEN = '2026-10-24T00:00:00Z', EXPIRES = '2099-10-25T00:00:00Z', PRIVATE = 'PRIVATE_SYNTHETIC_FEE_EVIDENCE';
const workflow = types.EXECUTION_ROLE_WORKFLOW, kind = types.EMPIRICAL_ACCOUNTING_KIND, clone = value => structuredClone(value);
const source = { brief_id: 'c'.repeat(32), brief_revision: 1, source_version: 1, source_hash: HASH, snapshot_hash: SNAP, title: '合成风险授权资料', production: { duration_seconds: 15, ratio: '9:16', style: 'realistic_cinematic' }, budget_cny: null, currency: 'CNY', frozen_at: WHEN };
const plan = { source_snapshot_hash: SNAP, duration_ms: 15000, ratio: '9:16', shots: [{ id: 'S1', duration_ms: 15000, visual: '合成画面', camera: '', audio: '', depends_on: [] }] };
const policy = { kind, role_workflow: workflow, id: 'synthetic-empirical-policy', version: 'v1', policy_hash: HASH, provider: 'openai_compatible', model: 'synthetic-only', endpoint: 'https://synthetic.invalid/v1/chat/completions', currency: 'CNY', wire_api: 'chat_completions', chat_output_parameter: 'max_tokens',
  input_cny_per_million: '0.500000', output_cny_per_million: '1.000000', max_output_tokens: 1000, max_input_bytes: 32768, max_output_bytes: 65536,
  request_deadline_seconds: 10, max_calls: 6, task_limit_cny: '6.000000', account_limit_cny: '75.370000', account_max_calls: 47, valid_until: EXPIRES,
  privacy_scope: types.EMPIRICAL_EXECUTION_SCOPE, pricing_basis: 'synthetic estimate only', per_call_risk_cny: '1.000000', risk_basis: 'empirical_not_hard_billing_bound', parent_anchor_hash: ANCHOR };
function quote() { return { accounting_kind: kind, parent_anchor_hash: ANCHOR, per_call_risk_cny: '1.000000', role_workflow: workflow, role_scope: clone(types.EXECUTION_ROLE_SCOPE), id: Q, task_id: A, planner_task_revision: 1, snapshot_hash: SNAP, policy: clone(policy), per_call_hold_cny: '1.000000', total_limit_cny: '6.000000', max_calls: 6, expires_at: EXPIRES, disclosure_hash: HASH, created_at: WHEN, status: 'valid' }; }
function grant() { return { accounting_kind: kind, parent_anchor_hash: ANCHOR, role_workflow: workflow, role_scope_hash: roles.EXECUTION_ROLE_SCOPE_HASH, id: G, task_id: A, quote_id: Q, snapshot_hash: SNAP, policy_hash: HASH, disclosure_hash: HASH, scope: types.EMPIRICAL_EXECUTION_SCOPE, status: 'active', total_limit_cny: '6.000000', max_calls: 6, expires_at: EXPIRES, created_at: WHEN, revoked_at: null }; }
function roleState() { return { schema_version: 1, status: 'awaiting_plan', roles: ['planner','reviewer','repairer','rechecker'], planner_call_count: 0, reviewer_call_count: 0, repairer_call_count: 0, rechecker_call_count: 0, max_total_calls: 6, max_reviewer_calls: 1, max_repairer_calls: 1, max_rechecker_calls: 1,
  first_review_binding: null, first_report_hash: null, first_verdict: null, repair_binding: null, repair_task_hash: null, recheck_binding: null, recheck_report_hash: null, recheck_verdict: null, accepted_plan_version: null, plans: [] }; }
function ready() { return { accounting_kind: kind, parent_anchor_hash: ANCHOR, role_workflow: workflow, role_state: roleState(), task_id: A, revision: 2, environment: 'synthetic_empirical_test', status: 'ready', blocked_reason: null, allowed_actions: ['resume'], source, policy_available: true, active_grant_id: G, quotes: [quote()], grants: [grant()],
  ledger: { accounting_kind: kind, opening_exposure_cny: '69.370000', opening_call_count: 41, risk_allocated_cny: '0.000000', local_estimate_cny: '0.000000', platform_observed_cny: '0.000000', account_risk_allocated_cny: '69.370000', account_local_estimate_cny: '0.000000', account_platform_observed_cny: '0.000000',
    currency: 'CNY', locally_accounted_cny: '0.000000', held_cny: '0.000000', task_limit_cny: '6.000000', call_count: 0, max_calls: 6, account_locally_accounted_cny: '0.000000', account_held_cny: '69.370000', account_limit_cny: '75.370000', account_call_count: 41, account_max_calls: 47, provider_bill_confirmed: false }, operations: [], plan: null, quality_status: 'not_reviewed', created_at: WHEN, updated_at: WHEN }; }
function prequote() {
  const value = ready();
  for (const field of ['accounting_kind','parent_anchor_hash','role_workflow','role_state']) delete value[field];
  for (const field of ['accounting_kind','opening_exposure_cny','opening_call_count','risk_allocated_cny','local_estimate_cny','platform_observed_cny','account_risk_allocated_cny','account_local_estimate_cny','account_platform_observed_cny']) delete value.ledger[field];
  Object.assign(value, { revision: 0, status: 'not_authorized', blocked_reason: 'AUTHORIZATION_REQUIRED', allowed_actions: [], active_grant_id: null, quotes: [], grants: [], created_at: null, updated_at: null });
  return value;
}
function operation() { return { accounting_kind: kind, parent_anchor_hash: ANCHOR, risk_cny: '1.000000', local_estimate_cny: '0.010000', platform_observed_cny: null, fee_status: 'awaiting_platform_fee', receipt_classification: 'complete', output_limit_observed: true,
  role_workflow: workflow, role: 'planner', role_call_index: 1, role_binding_hash: HASH, wire_api: 'chat_completions', chat_output_parameter: 'max_tokens', id: OP, step: 1, status: 'received', grant_id: G, quote_id: Q, policy_hash: HASH, context_hash: HASH,
  hold_cny: '1.000000', locally_accounted_cny: '0.010000', provider_request_id: 'synthetic-request', provider_response_id: 'synthetic-response', input_tokens: 100, output_tokens: 100, finish_reason: 'stop', receipt_status: 'received', created_at: WHEN, received_at: WHEN }; }
function awaiting() { const value = ready(); Object.assign(value, { revision: 3, status: 'needs_reconciliation', blocked_reason: 'PLATFORM_FEE_REQUIRED', allowed_actions: ['stop'], operations: [operation()] }); value.role_state.planner_call_count = 1;
  Object.assign(value.ledger, { platform_observed_cny: null, account_platform_observed_cny: null, risk_allocated_cny: '1.000000', held_cny: '1.000000', call_count: 1, local_estimate_cny: '0.010000', locally_accounted_cny: '0.010000', account_risk_allocated_cny: '70.370000', account_held_cny: '70.370000', account_call_count: 42, account_local_estimate_cny: '0.010000', account_locally_accounted_cny: '0.010000' }); return value; }
function matched({ overrun = false, truncated = false } = {}) { const value = awaiting(), op = value.operations[0], fee = overrun ? '1.100000' : '0.020000';
  Object.assign(op, { fee_status: overrun ? 'overrun' : 'matched', platform_observed_cny: fee, receipt_classification: truncated ? 'truncated' : 'complete' });
  Object.assign(value.ledger, { platform_observed_cny: fee, account_platform_observed_cny: fee });
  Object.assign(value, { status: overrun ? 'blocked' : truncated ? 'needs_reconciliation' : 'stopped', blocked_reason: overrun ? 'EMPIRICAL_RISK_OVERRUN' : truncated ? 'RECEIPT_TRUNCATED' : 'STOPPED', allowed_actions: overrun || truncated ? [] : ['resume'] }); return value; }
function storage() { const values = new Map(); return { values, getItem: key => values.get(key) ?? null, setItem: (key,value) => values.set(key,value), removeItem: key => values.delete(key) }; }
const binding = { id: A, revision: 1, mode: 'normal', source };
const reads = (url,value, p = policy) => url.endsWith('/execution/policy') ? Response.json({ available: true, blocked_reason: null, environment: 'synthetic_empirical_test', policy: p }) : url.includes('/events?') ? Response.json({ events: [], next_sequence: 0, has_more: false }) : Response.json({ execution: value });

test('empirical policy is explicit, bounded to six calls and never projects a hard billing bound', () => {
  const p = api.readExecutionPolicy(policy); assert.equal(p.kind, kind); assert.equal(p.privacy_scope, types.EMPIRICAL_EXECUTION_SCOPE);
  for (const field of ['max_input_tokens','input_bound_basis','billing_bound_verified']) assert.equal(field in p, false);
  for (const mutate of [p => { delete p.kind; },p => { delete p.parent_anchor_hash; },p => { delete p.role_workflow; },p => { delete p.wire_api; },p => { delete p.chat_output_parameter; },p => { p.kind = 'unknown'; },p => { p.privacy_scope = types.EXECUTION_SCOPE; },p => { p.max_calls = 7; },p => { p.per_call_risk_cny = '0.500000'; }, ...['max_input_tokens','input_bound_basis','billing_bound_verified'].map(field => p => { p[field] = field === 'max_input_tokens' ? 1000 : true; })]) { const p = clone(policy); mutate(p); assert.throws(() => api.readExecutionPolicy(p)); }
  const p2 = clone(policy); p2.max_calls = 3; assert.equal(api.readExecutionPolicy(p2).max_calls, 3);
});

test('pristine prospective empirical environment decodes without inventing frozen profile tags', () => {
  for (const environment of ['synthetic_empirical_test','empirical_live']) {
    const value = prequote(); value.environment = environment;
    const read = api.readExecutionDetail(value);
    assert.equal(risk.executionIsPristine(read), true); assert.equal(risk.executionPolicyMatchesProfile(read, policy), true);
    for (const field of ['role_workflow','role_state','accounting_kind','parent_anchor_hash']) assert.equal(field in read, false);
    for (const mutate of [v => { v.revision = 1; },v => { v.status = 'ready'; },v => { v.active_grant_id = G; },v => { v.ledger.call_count = 1; },v => { v.ledger.held_cny = '1.000000'; },v => { v.ledger.locally_accounted_cny = '0.010000'; }]) { const bad = clone(value); mutate(bad); assert.throws(() => api.readExecutionDetail(bad)); }
  }
  for (const mutate of [v => { v.quotes = [quote()]; },v => { v.grants = [grant()]; },v => { v.operations = [operation()]; },v => { v.role_workflow = workflow; },v => { v.accounting_kind = kind; }]) { const bad = prequote(); mutate(bad); assert.equal(risk.executionIsPristine(bad), false); assert.throws(() => api.readExecutionDetail(bad)); }
});

test('fresh normal task initializes then quotes and explicitly grants without starting or upgrading pending consent', async t => {
  const s = storage(), c = new control.PlannerExecutionController(() => s); let current = prequote(); const posts = [];
  t.mock.method(globalThis, 'fetch', async (url,options) => {
    if (options.method === 'POST') {
      const body = JSON.parse(options.body); posts.push({ url, body });
      if (url.endsWith('/quotes')) {
        assert.equal(body.expected_execution_revision, 0); assert.equal(body.expected_task_revision, 1);
        current = ready(); Object.assign(current, { revision: 1, status: 'not_authorized', blocked_reason: 'AUTHORIZATION_REQUIRED', allowed_actions: [], active_grant_id: null, grants: [] });
        return Response.json({ execution: current, quote: quote(), replayed: false, applied_revision: 1 });
      }
      assert.ok(url.endsWith('/grants')); assert.equal(body.accepted_scope, types.EMPIRICAL_EXECUTION_SCOPE); assert.equal(body.accepted_disclosure_hash, HASH); assert.equal(body.expected_execution_revision, 1);
      current = ready(); return Response.json({ execution: current, grant: grant(), replayed: false, applied_revision: 2 });
    }
    return reads(url,current);
  });
  await c.initialize(binding); assert.equal(c.getSnapshot().error, ''); assert.equal(c.getSnapshot().execution.revision, 0); assert.equal(c.getSnapshot().execution.accounting_kind, undefined);
  await c.requestQuote(); assert.equal(posts.length, 1); assert.equal(c.getSnapshot().execution.accounting_kind, kind); assert.equal(c.getSnapshot().execution.grants.length, 0); assert.equal(c.getSnapshot().pending, null);
  c.reviewQuote(true); assert.ok(c.getSnapshot().reviewed); await c.grant();
  assert.equal(posts.length, 2); assert.equal(posts.some(post => post.url.endsWith('/actions')), false); assert.equal(c.getSnapshot().execution.status, 'ready'); assert.equal(c.getSnapshot().execution.operations.length, 0); assert.equal(c.getSnapshot().pending, null);
  assert.equal(s.getItem(c.storageKey), null);
});

test('partial risk tags, scope substitution and mixed environments cannot downgrade to legacy', () => {
  const mutations = [v => { delete v.accounting_kind; },v => { delete v.parent_anchor_hash; },v => { delete v.role_state; },v => { delete v.quotes[0].accounting_kind; },v => { delete v.quotes[0].per_call_risk_cny; },v => { delete v.grants[0].parent_anchor_hash; },v => { v.grants[0].scope = types.EXECUTION_SCOPE; },v => { delete v.ledger.accounting_kind; },v => { delete v.operations[0].risk_cny; },v => { v.operations[0].parent_anchor_hash = HASH; },v => { v.environment = 'live'; }];
  for (const mutate of mutations) { const value = awaiting(); mutate(value); assert.throws(() => api.readExecutionDetail(value)); }
  assert.equal(api.readExecutionDetail({ ...ready(), environment: 'empirical_live' }).environment, 'empirical_live');
});

test('risk is permanently retained after fee match or overrun, independent of both observed and estimated charges', () => {
  for (const value of [ready(),awaiting(),matched(),matched({ overrun: true })]) {
    const result = api.readExecutionDetail(value); assert.deepEqual(result.ledger, value.ledger); assert.equal(result.quality_status, 'not_reviewed'); assert.equal(result.ledger.provider_bill_confirmed, false);
    assert.equal(result.ledger.account_call_count, 41 + result.operations.length); assert.equal(result.ledger.risk_allocated_cny, result.operations.length ? '1.000000' : '0.000000');
  }
  const read = api.readExecutionDetail(matched({ overrun: true })); assert.equal(read.operations[0].platform_observed_cny, '1.100000'); assert.equal(read.operations[0].risk_cny, '1.000000');
  for (const mutate of [v => { v.ledger.held_cny = '0.000000'; },v => { v.ledger.risk_allocated_cny = '0.000000'; },v => { v.ledger.local_estimate_cny = '0.020000'; },v => { v.ledger.platform_observed_cny = '0.000000'; },v => { v.operations[0].fee_status = 'matched'; },v => { v.operations[0].risk_cny = '1.100000'; }]) { const value = matched({ overrun: true }); mutate(value); assert.throws(() => api.readExecutionDetail(value)); }
});

test('opening baseline is frozen metadata, not a hardcoded grant or invented historical fee', () => {
  const value = ready(); Object.assign(value.ledger, { opening_exposure_cny: '5.500000', opening_call_count: 7, account_risk_allocated_cny: '5.500000', account_held_cny: '5.500000', account_call_count: 7 });
  const read = api.readExecutionDetail(value); assert.equal(read.ledger.opening_call_count, 7); assert.equal(read.ledger.account_local_estimate_cny, '0.000000'); assert.equal(read.grants[0].scope, types.EMPIRICAL_EXECUTION_SCOPE);
});

test('known local or platform amount above risk requires overrun even while platform fee is unknown or lower', () => {
  for (const platform of [null,'0.020000','1.100000']) {
    const value = matched({ overrun: true }), op = value.operations[0];
    Object.assign(op, { local_estimate_cny: '1.200000', locally_accounted_cny: '1.200000', platform_observed_cny: platform });
    Object.assign(value.ledger, { local_estimate_cny: '1.200000', locally_accounted_cny: '1.200000', account_local_estimate_cny: '1.200000', account_locally_accounted_cny: '1.200000', platform_observed_cny: platform, account_platform_observed_cny: platform });
    const read = api.readExecutionDetail(value);
    assert.equal(read.operations[0].fee_status, 'overrun'); assert.equal(read.operations[0].platform_observed_cny, platform); assert.equal(read.operations[0].local_estimate_cny, '1.200000'); assert.equal(read.ledger.held_cny, '1.000000'); assert.equal(control.executionHasUnknownCall(read), true);
    for (const fee of ['awaiting_receipt','awaiting_platform_fee','matched']) { const bad = clone(value); bad.operations[0].fee_status = fee; assert.throws(() => api.readExecutionDetail(bad)); }
  }
  const noExcess = matched(); noExcess.operations[0].fee_status = 'overrun'; assert.throws(() => api.readExecutionDetail(noExcess));
});

test('complete HTTP receipt and local estimate cannot process a plan or next role before server fee match', () => {
  const value = awaiting(); assert.equal(api.readExecutionDetail(value).plan, null); assert.equal(control.executionHasUnknownCall(api.readExecutionDetail(value)), true);
  for (const mutate of [v => { v.plan = clone(plan); v.role_state.plans = [{ version: 1, plan_hash: HASH }]; v.role_state.status = 'review_ready'; },v => { v.allowed_actions = ['resume']; },v => { v.allowed_actions = ['reconcile']; },v => { v.operations[0].fee_status = 'matched'; }]) { const value = awaiting(); mutate(value); assert.throws(() => api.readExecutionDetail(value)); }
});

test('unknown fee/estimate/output observation stay null and only explicit observed zero remains zero', () => {
  const value = awaiting(); Object.assign(value.operations[0], { local_estimate_cny: null, locally_accounted_cny: null, platform_observed_cny: null, output_limit_observed: null });
  Object.assign(value.ledger, { local_estimate_cny: null, locally_accounted_cny: '0.000000', account_local_estimate_cny: null, account_locally_accounted_cny: '0.000000' });
  const read = api.readExecutionDetail(value); assert.equal(read.operations[0].local_estimate_cny, null); assert.equal(read.operations[0].platform_observed_cny, null); assert.equal(read.operations[0].output_limit_observed, null);
  const zero = matched(); zero.operations[0].platform_observed_cny = '0.000000'; zero.ledger.platform_observed_cny = zero.ledger.account_platform_observed_cny = '0.000000'; assert.equal(api.readExecutionDetail(zero).operations[0].platform_observed_cny, '0.000000');
  for (const field of ['local_estimate_cny','platform_observed_cny','account_local_estimate_cny','account_platform_observed_cny']) { const bad = clone(value); bad.ledger[field] = '0.000000'; assert.throws(() => api.readExecutionDetail(bad)); }
});

test('current empirical policy cannot quote or authorize an existing formal role profile', async t => {
  const formal = ready(); delete formal.accounting_kind; delete formal.parent_anchor_hash; formal.environment = 'live';
  formal.quotes = []; formal.grants = []; formal.active_grant_id = null; formal.status = 'not_authorized'; formal.allowed_actions = [];
  for (const field of ['accounting_kind','opening_exposure_cny','opening_call_count','risk_allocated_cny','local_estimate_cny','platform_observed_cny','account_risk_allocated_cny','account_local_estimate_cny','account_platform_observed_cny']) delete formal.ledger[field];
  const c = new control.PlannerExecutionController(() => storage()); let writes = 0;
  t.mock.method(globalThis, 'fetch', async (url,options) => { if (options.method === 'POST') writes++; return reads(url,formal); });
  await c.initialize(binding); await c.requestQuote(); c.reviewQuote(true); await c.grant(); assert.equal(writes, 0); assert.match(c.getSnapshot().error, /不能升级/); assert.equal(c.getSnapshot().reviewed, null);
});

test('pending, truncated and overrun stale actions cannot trigger resume or client fee reconciliation', async t => {
  for (const value of [awaiting(),matched({ truncated: true }),matched({ overrun: true })]) {
    const c = new control.PlannerExecutionController(() => storage()); let writes = 0;
    t.mock.method(globalThis, 'fetch', async (url,options) => { if (options.method === 'POST') writes++; return reads(url,value); });
    await c.initialize(binding); c.getSnapshot().execution.allowed_actions = ['resume','reconcile','stop'];
    await c.action('resume'); await c.refresh(); c.getSnapshot().execution.allowed_actions = ['reconcile']; await c.action('reconcile'); assert.equal(writes, 0);
  }
});

test('matched complete receipt on a paused task retains legitimate original-worker resume', async t => {
  const value = matched(), c = new control.PlannerExecutionController(() => storage()); let writes = 0;
  assert.equal(control.executionHasUnknownCall(api.readExecutionDetail(value)), false);
  t.mock.method(globalThis, 'fetch', async (url,options) => { if (options.method === 'POST') { writes++; assert.equal(JSON.parse(options.body).action, 'resume'); return Response.json({ execution: value, replayed: false, applied_revision: value.revision }); } return reads(url,value); });
  await c.initialize(binding); await c.action('resume'); assert.equal(writes, 1); assert.equal(c.getSnapshot().pending, null);
});

test('explicit new disclosure uses new scope only, stores no fee authority, and never starts automatically', async t => {
  const s = storage(), c = new control.PlannerExecutionController(() => s), current = ready(); current.grants = []; current.active_grant_id = null; current.status = 'not_authorized'; current.allowed_actions = []; let writes = 0;
  t.mock.method(globalThis, 'fetch', async (url,options) => {
    if (options.method === 'POST') { writes++; assert.ok(url.endsWith('/grants')); const body = JSON.parse(options.body); assert.equal(body.accepted_scope, types.EMPIRICAL_EXECUTION_SCOPE); assert.equal(body.accepted_disclosure_hash, HASH); assert.deepEqual(Object.keys(body).sort(), ['accepted_disclosure_hash','accepted_scope','client_request_key','consent','expected_execution_revision','quote_id']); return Response.json({ execution: ready(), grant: grant(), replayed: false, applied_revision: 2 }); }
    return reads(url,current);
  });
  await c.initialize(binding); c.reviewQuote(true); await c.grant(); assert.equal(writes, 1); assert.equal(c.getSnapshot().execution.operations.length, 0); assert.equal(s.getItem(c.storageKey), null);
});

test('old and new pending consent retain their exact scope across current policy changes', async t => {
  for (const scope of [types.EXECUTION_SCOPE,types.EMPIRICAL_EXECUTION_SCOPE]) {
    const s = storage(), c = new control.PlannerExecutionController(() => s), intent = { version: 1, kind: 'grant', task_id: A, snapshot_hash: SNAP, request: { client_request_key: 'original_pending_key', expected_execution_revision: 1, quote_id: Q, accepted_disclosure_hash: HASH, accepted_scope: scope, consent: true } };
    s.setItem(c.storageKey, JSON.stringify(intent)); let posted;
    t.mock.method(globalThis, 'fetch', async (url,options) => { if (url.includes('/by-key/')) return Response.json({ detail: { code: 'NOT_FOUND' } }, { status: 404 }); if (options.method === 'POST') { posted = JSON.parse(options.body); throw new TypeError('lost'); } return reads(url, ready()); });
    await c.initialize(binding); await c.recover(true); assert.deepEqual(posted, intent.request); assert.deepEqual(JSON.parse(s.getItem(c.storageKey)), intent);
  }
});

test('malformed risk metadata preserves pending original request and never automatically retries', async t => {
  const s = storage(), c = new control.PlannerExecutionController(() => s); let writes = 0, input;
  t.mock.method(globalThis, 'fetch', async (url,options) => { if (options.method === 'POST') { writes++; input = JSON.parse(options.body); const bad = awaiting(); delete bad.operations[0].fee_status; return Response.json({ execution: bad, replayed: false, applied_revision: 3 }); } return reads(url, writes ? awaiting() : ready()); });
  await c.initialize(binding); await c.action('resume'); const original = s.getItem(c.storageKey); assert.ok(original); await c.recover(false); await c.action('resume'); assert.equal(writes, 1); assert.equal(s.getItem(c.storageKey), original); assert.deepEqual(c.getSnapshot().pending.request, input); assert.equal(c.getSnapshot().execution.ledger.held_cny, '1.000000');
});

test('safe risk projection strips raw platform evidence and empirical environment remains task frozen', () => {
  const value = awaiting(); value.operations[0].platform_evidence = PRIVATE; value.operations[0].raw = PRIVATE;
  assert.equal(JSON.stringify(api.readExecutionDetail(value)).includes(PRIVATE), false);
  assert.equal(view.observedExecutionEnvironment({ environment: 'empirical_live' }), 'empirical_live'); assert.equal(view.isSyntheticExecutionEnvironment('empirical_live'), false); assert.equal(view.isSyntheticExecutionEnvironment('synthetic_empirical_test'), true); assert.equal(view.isSyntheticExecutionEnvironment(null), false);
});
