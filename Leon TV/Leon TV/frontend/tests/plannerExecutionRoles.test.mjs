import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: api } = await loadModule('lib/plannerExecutionApi.ts');
const { module: types } = await loadModule('lib/plannerExecutionTypes.ts');
const { module: roles } = await loadModule('lib/plannerExecutionRoles.ts');
const { module: control } = await loadModule('lib/plannerExecutionController.ts');
const { module: briefApi } = await loadModule('lib/briefPlannerApi.ts');
const A = 'a'.repeat(32), B = 'b'.repeat(32), BRIEF = 'c'.repeat(32), SNAP = 'd'.repeat(64), HASH = 'e'.repeat(64), Q = '1'.repeat(32), G = '2'.repeat(32);
const WHEN = '2026-10-25T00:00:00Z', EXPIRES = '2099-10-25T00:00:00Z', PRIVATE = 'PRIVATE_SYNTHETIC_ROLE_TEXT';
const workflow = types.EXECUTION_ROLE_WORKFLOW, clone = value => structuredClone(value);
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(', ')}]` : value && typeof value === 'object' ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}: ${canonical(value[key])}`).join(', ')}}` : JSON.stringify(value);
const digest = value => createHash('sha256').update(canonical(value)).digest('hex');
const source = { brief_id: BRIEF, brief_revision: 1, source_version: 1, source_hash: HASH, snapshot_hash: SNAP, title: '合成授权资料', production: { duration_seconds: 15, ratio: '9:16', style: 'realistic_cinematic' }, budget_cny: null, currency: 'CNY', frozen_at: WHEN };
const parent = { source_snapshot_hash: SNAP, duration_ms: 15000, ratio: '9:16', shots: [{ id: 'S1', duration_ms: 15000, visual: '合成画面', camera: '合成运镜', audio: '', depends_on: [] }] };
const candidate = clone(parent); candidate.shots[0].camera = '合成修订运镜';
const policy = { role_workflow: workflow, id: 'synthetic-role-policy', version: 'v1', policy_hash: HASH, provider: 'openai_compatible', model: 'synthetic-only', endpoint: 'https://synthetic.invalid/v1/chat/completions', currency: 'CNY',
  input_cny_per_million: '0.500000', output_cny_per_million: '1.000000', max_input_tokens: 1000, max_output_tokens: 1000, max_input_bytes: 32768, max_output_bytes: 65536,
  request_deadline_seconds: 10, max_calls: 6, task_limit_cny: '1.000000', account_limit_cny: '2.000000', account_max_calls: 12, valid_until: EXPIRES,
  privacy_scope: types.EXECUTION_SCOPE, pricing_basis: 'synthetic test only', input_bound_basis: 'synthetic bound only' };
function quote() { return { role_workflow: workflow, role_scope: clone(types.EXECUTION_ROLE_SCOPE), id: Q, task_id: A, planner_task_revision: 1, snapshot_hash: SNAP, policy: clone(policy), per_call_hold_cny: '0.001500', total_limit_cny: '1.000000', max_calls: 6, expires_at: EXPIRES, disclosure_hash: HASH, created_at: WHEN, status: 'valid' }; }
function grant() { return { role_workflow: workflow, role_scope_hash: roles.EXECUTION_ROLE_SCOPE_HASH, id: G, task_id: A, quote_id: Q, snapshot_hash: SNAP, policy_hash: HASH, disclosure_hash: HASH, scope: types.EXECUTION_SCOPE, status: 'active', total_limit_cny: '1.000000', max_calls: 6, expires_at: EXPIRES, created_at: WHEN, revoked_at: null }; }
function roleState() { return { schema_version: 1, status: 'awaiting_plan', roles: ['planner','reviewer','repairer','rechecker'], planner_call_count: 0, reviewer_call_count: 0, repairer_call_count: 0, rechecker_call_count: 0,
  max_total_calls: 6, max_reviewer_calls: 1, max_repairer_calls: 1, max_rechecker_calls: 1, first_review_binding: null, first_report_hash: null, first_verdict: null,
  repair_binding: null, repair_task_hash: null, recheck_binding: null, recheck_report_hash: null, recheck_verdict: null, accepted_plan_version: null, plans: [] }; }
function execution() { return { role_workflow: workflow, role_state: roleState(), task_id: A, revision: 2, environment: 'synthetic_test', status: 'ready', blocked_reason: null, allowed_actions: ['resume'], source, policy_available: true, active_grant_id: G,
  quotes: [quote()], grants: [grant()], ledger: { currency: 'CNY', locally_accounted_cny: '0.000000', held_cny: '0.000000', task_limit_cny: '1.000000', call_count: 0, max_calls: 6,
    account_locally_accounted_cny: '0.000000', account_held_cny: '0.000000', account_limit_cny: '2.000000', account_call_count: 0, account_max_calls: 12, provider_bill_confirmed: false }, operations: [], plan: null, quality_status: 'not_reviewed', created_at: WHEN, updated_at: WHEN }; }
function operation(role, step, index = 1) { return { role_workflow: workflow, role, role_call_index: index, role_binding_hash: HASH, id: String(step + 2).repeat(32), step, status: 'settled', grant_id: G, quote_id: Q, policy_hash: HASH, context_hash: HASH,
  hold_cny: '0.001500', locally_accounted_cny: '0.001000', provider_request_id: `synthetic-request-${step}`, provider_response_id: `synthetic-response-${step}`, input_tokens: 1, output_tokens: 1, finish_reason: 'stop', receipt_status: 'confirmed_local', created_at: WHEN, received_at: WHEN }; }
function completed(outcome = 'accepted') {
  const value = execution(), s = value.role_state;
  value.revision = 12; value.status = outcome === 'accepted' ? 'plan_ready' : 'blocked'; value.allowed_actions = []; value.plan = clone(candidate);
  value.blocked_reason = outcome === 'accepted' ? null : outcome === 'rejected' ? 'RECHECK_REQUIRES_REPAIR' : 'RECHECK_NEEDS_INPUT';
  value.operations = [operation('planner',1,1),operation('planner',2,2),operation('reviewer',3),operation('repairer',4),operation('rechecker',5)];
  Object.assign(value.ledger, { call_count: 5, account_call_count: 5, locally_accounted_cny: '0.005000', account_locally_accounted_cny: '0.005000' });
  const first = { review_id: 'first-review', run_id: A, intent_id: value.operations[2].id, task_id: A, plan_version: 1, plan_hash: digest(parent), source_snapshot_hash: SNAP, rule_version: 'D15-RV-v0.2', rule_hash: briefApi.PLANNER_REVIEW_RULE_HASH };
  const repair = { task_id: A, run_id: A, intent_id: value.operations[3].id, source_snapshot_hash: SNAP, parent_plan_version: 1, parent_plan_hash: digest(parent), review_id: first.review_id, report_hash: '8'.repeat(64), repair_task_hash: '9'.repeat(64), next_plan_version: 2 };
  const recheck = { ...clone(first), review_id: 'recheck-review', intent_id: value.operations[4].id, plan_version: 2, plan_hash: digest(candidate) };
  value.operations[2].role_binding_hash = digest(first); value.operations[3].role_binding_hash = digest(repair); value.operations[4].role_binding_hash = digest(recheck);
  Object.assign(s, { status: outcome, planner_call_count: 2, reviewer_call_count: 1, repairer_call_count: 1, rechecker_call_count: 1, first_review_binding: first, first_report_hash: '8'.repeat(64), first_verdict: 'rejected',
    repair_binding: repair, repair_task_hash: '9'.repeat(64), recheck_binding: recheck, recheck_report_hash: '7'.repeat(64), recheck_verdict: outcome, accepted_plan_version: outcome === 'accepted' ? 2 : null,
    plans: [{ version: 1, plan_hash: digest(parent) },{ version: 2, plan_hash: digest(candidate) }] });
  return value;
}
function unknown(role = 'repairer') {
  const value = completed(), s = value.role_state, keep = role === 'reviewer' ? 3 : role === 'repairer' ? 4 : 5;
  value.status = 'needs_reconciliation'; value.blocked_reason = 'RESPONSE_UNCONFIRMED'; value.allowed_actions = ['stop']; value.operations = value.operations.slice(0,keep);
  Object.assign(value.operations.at(-1), { status: 'needs_reconciliation', locally_accounted_cny: null, receipt_status: 'unconfirmed', provider_request_id: null, provider_response_id: null, input_tokens: null, output_tokens: null, finish_reason: null, received_at: null });
  Object.assign(value.ledger, { call_count: keep, account_call_count: keep, held_cny: '0.001500', account_held_cny: '0.001500' });
  Object.assign(s, { status: `${role === 'reviewer' ? 'review' : role === 'repairer' ? 'repair' : 'recheck'}_response_unknown`, accepted_plan_version: null, recheck_report_hash: null, recheck_verdict: null });
  if (role !== 'rechecker') { value.plan = clone(parent); s.plans.pop(); s.rechecker_call_count = 0; s.recheck_binding = null; }
  if (role === 'reviewer') Object.assign(s, { first_report_hash: null, first_verdict: null, repair_binding: null, repair_task_hash: null, repairer_call_count: 0 });
  return value;
}
function storage() { const values = new Map(); return { values, getItem: key => values.get(key) ?? null, setItem: (key,value) => values.set(key,value), removeItem: key => values.delete(key) }; }
const binding = { id: A, revision: 1, mode: 'normal', source };
const events = () => Response.json({ events: [], next_sequence: 0, has_more: false });
const reads = (url,value) => url.endsWith('/execution/policy') ? Response.json({ available: true, blocked_reason: null, environment: 'synthetic_test', policy }) : url.includes('/events?') ? events() : Response.json({ execution: value });

test('new opt-in policy and complete fixed role disclosure preserve legacy scope and server hashes', () => {
  assert.equal(digest(types.EXECUTION_ROLE_SCOPE), roles.EXECUTION_ROLE_SCOPE_HASH);
  const q = api.readExecutionQuote(quote()), g = api.readExecutionGrant(grant());
  assert.equal(q.role_workflow, workflow); assert.deepEqual(q.role_scope, types.EXECUTION_ROLE_SCOPE);
  assert.equal(g.role_scope_hash, roles.EXECUTION_ROLE_SCOPE_HASH); assert.equal(g.scope, types.EXECUTION_SCOPE);
  for (const value of [null,'unknown-workflow',false]) assert.throws(() => api.readExecutionPolicy({ ...policy, role_workflow: value }));
  for (const mutate of [q => { delete q.role_scope; },q => { delete q.role_workflow; },q => { delete q.policy.role_workflow; },q => { q.role_scope.data.pop(); },q => { q.role_scope.max_total_calls = 7; }]) { const q = quote(); mutate(q); assert.throws(() => api.readExecutionQuote(q)); }
});

test('ready, current-version accepted, rejected and unknown safe metadata decode without altering fees', () => {
  assert.equal(api.readExecutionDetail(execution()).role_state.status, 'awaiting_plan');
  for (const outcome of ['accepted','rejected','unknown']) {
    const value = completed(outcome), read = api.readExecutionDetail(value);
    assert.equal(read.role_state.recheck_verdict, outcome); assert.equal(read.quality_status, 'not_reviewed');
    assert.deepEqual(read.ledger, value.ledger); assert.equal(read.ledger.provider_bill_confirmed, false);
    assert.equal(read.role_state.accepted_plan_version, outcome === 'accepted' ? 2 : null);
  }
});

test('missing role state or partial quote/grant/operation tags cannot silently downgrade to legacy', () => {
  const mutations = [
    value => { delete value.role_state; },value => { delete value.role_workflow; },value => { delete value.quotes[0].role_scope; },
    value => { delete value.grants[0].role_scope_hash; },value => { delete value.grants[0].role_workflow; },
    value => { delete value.operations[0].role; },value => { delete value.operations[0].role_workflow; },value => { delete value.operations[0].role_binding_hash; },
    value => { value.role_state = null; },value => { value.grants[0].role_scope_hash = '0'.repeat(64); },
  ];
  for (const mutate of mutations) { const value = completed(); mutate(value); assert.throws(() => api.readExecutionDetail(value)); }
});

test('role counters, serial role order and per-role indices must match actual operation metadata', () => {
  for (const mutate of [
    value => { value.role_state.planner_call_count = 3; },value => { value.ledger.call_count = 4; },value => { value.role_state.max_total_calls = 7; },
    value => { value.operations[1].role_call_index = 3; },value => { value.operations[3].role_call_index = 2; },
    value => { [value.operations[2],value.operations[3]] = [value.operations[3],value.operations[2]]; },
    value => { value.operations[0].role = 'reviewer'; },value => { value.operations[4].role = 'planner'; },
  ]) { const value = completed(); mutate(value); assert.throws(() => api.readExecutionDetail(value)); }
});

test('parent/current plan metadata and intent bindings cannot borrow old-version acceptance', () => {
  for (const mutate of [
    value => { value.role_state.accepted_plan_version = 1; },value => { value.role_state.plans[1].plan_hash = '0'.repeat(64); },
    value => { value.role_state.recheck_binding.plan_version = 1; },value => { value.role_state.first_review_binding.source_snapshot_hash = '0'.repeat(64); },
    value => { value.role_state.repair_binding.report_hash = '0'.repeat(64); },value => { value.role_state.repair_task_hash = '0'.repeat(64); },
    value => { value.role_state.recheck_binding.intent_id = B; },value => { value.role_state.recheck_binding.review_id = value.role_state.first_review_binding.review_id; },
    value => { value.role_state.recheck_verdict = 'rejected'; },value => { value.plan = null; },
    value => { value.role_state.status = 'awaiting_plan'; value.status = 'running'; value.role_state.accepted_plan_version = null; },
  ]) { const value = completed(); mutate(value); assert.throws(() => api.readExecutionDetail(value)); }
});

test('unknown role receipts keep original shared hold and null usage/amount instead of reporting zero', () => {
  for (const role of ['reviewer','repairer','rechecker']) {
    const value = api.readExecutionDetail(unknown(role));
    assert.equal(value.operations.at(-1).locally_accounted_cny, null); assert.equal(value.operations.at(-1).input_tokens, null);
    assert.equal(value.ledger.held_cny, '0.001500'); assert.equal(value.role_state.accepted_plan_version, null);
    assert.equal(control.executionHasUnknownCall(value), true);
  }
});

test('safe projection rejects role report bodies and drops private raw operation material', () => {
  const value = completed(); value.operations[0].response = PRIVATE; value.operations[0].reasoning = PRIVATE;
  const read = api.readExecutionDetail(value); assert.equal(JSON.stringify(read).includes(PRIVATE), false);
  value.role_state.first_report = { content: PRIVATE }; assert.throws(() => api.readExecutionDetail(value));
});

test('new grant still posts only the original consent references and never automatically starts', async t => {
  const s = storage(), c = new control.PlannerExecutionController(() => s); let current = execution(), posts = 0;
  current.revision = 1; current.grants = []; current.active_grant_id = null; current.status = 'not_authorized'; current.allowed_actions = [];
  t.mock.method(globalThis, 'fetch', async (url,options) => {
    if (options.method === 'POST') {
      posts++; assert.ok(url.endsWith('/grants'));
      const input = JSON.parse(options.body);
      assert.deepEqual(Object.keys(input).sort(), ['accepted_disclosure_hash','accepted_scope','client_request_key','consent','expected_execution_revision','quote_id']);
      assert.equal(input.accepted_scope, types.EXECUTION_SCOPE); assert.equal(input.accepted_disclosure_hash, HASH);
      current = execution(); return Response.json({ execution: current, grant: grant(), replayed: false, applied_revision: 2 });
    }
    return reads(url,current);
  });
  await c.initialize(binding); c.reviewQuote(true); await c.grant();
  assert.equal(posts, 1); assert.equal(c.getSnapshot().execution.status, 'ready'); assert.equal(c.getSnapshot().execution.operations.length, 0);
  assert.equal(c.getSnapshot().pending, null); assert.equal(JSON.stringify([...s.values]).includes('role_scope'), false);
});

test('malformed role metadata keeps the original action key, hold and exact recovery references', async t => {
  const s = storage(), c = new control.PlannerExecutionController(() => s); let current = execution(), posts = 0, original;
  t.mock.method(globalThis, 'fetch', async (url,options) => {
    if (options.method === 'POST') {
      posts++; original = JSON.parse(options.body); current = unknown('repairer');
      const malformed = clone(current); delete malformed.role_state;
      return Response.json({ execution: malformed, replayed: false, applied_revision: 3 });
    }
    return reads(url,current);
  });
  await c.initialize(binding); await c.action('resume');
  const pending = s.getItem(c.storageKey); assert.ok(pending); assert.equal(posts, 1);
  await c.recover(false); await c.action('resume');
  assert.equal(posts, 1); assert.equal(s.getItem(c.storageKey), pending); assert.deepEqual(c.getSnapshot().pending.request, original);
  assert.equal(c.getSnapshot().execution.ledger.held_cny, '0.001500'); assert.equal(c.getSnapshot().execution.role_state.repairer_call_count, 1);
});
