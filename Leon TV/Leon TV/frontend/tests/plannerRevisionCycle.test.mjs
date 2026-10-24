import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: api } = await loadModule('lib/briefPlannerApi.ts');
const { module: cycleModule } = await loadModule('lib/plannerRevisionCycle.ts');
const { module: control } = await loadModule('lib/briefPlannerController.ts');
const A = 'a'.repeat(32), B = 'b'.repeat(32), BRIEF = 'c'.repeat(32), SNAPSHOT = 'd'.repeat(64);
const FIRST_OP = 'f'.repeat(32), REPAIR_OP = '1'.repeat(32), RECHECK_OP = '2'.repeat(32), PRIVATE = 'SYNTHETIC_D24_DETAIL_ONLY';
const clone = value => structuredClone(value);
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(', ')}]` : value && typeof value === 'object' ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}: ${canonical(value[key])}`).join(', ')}}` : JSON.stringify(value);
const digest = value => createHash('sha256').update(canonical(value)).digest('hex');
const production = { duration_seconds: 15, ratio: '9:16', style: 'realistic_cinematic' };
const source = { brief_id: BRIEF, brief_revision: 1, source_version: 1, source_hash: 'e'.repeat(64), snapshot_hash: SNAPSHOT, title: 'D24 合成简报', production, budget_cny: null, currency: 'CNY', frozen_at: '2026-10-24T00:00:00Z' };
const parent = { source_snapshot_hash: SNAPSHOT, duration_ms: 15000, ratio: '9:16', shots: [
  { id: 'S1', duration_ms: 6000, visual: '纸船放上桌面。', camera: '原始合成运镜', audio: '', depends_on: [] },
  { id: 'S2', duration_ms: 9000, visual: '灯光熄灭。', camera: '固定镜头', audio: '', depends_on: ['S1'] },
] };
const candidate = clone(parent); candidate.shots[0].camera = '修订合成运镜 🎬';
const target = { shot_id: 'S1', field: 'camera' }, ruleIds = ['RV-F','RV-T','RV-C','RV-A','RV-G'];
const firstBinding = { review_id: 'first-review', run_id: A, intent_id: FIRST_OP, task_id: A, plan_version: 1, plan_hash: digest(parent), source_snapshot_hash: SNAPSHOT, rule_version: 'D15-RV-v0.2', rule_hash: api.PLANNER_REVIEW_RULE_HASH };
function makeRules(outcome, prior = null) {
  return ruleIds.map(rule_id => ({ rule_id, verdict: rule_id === 'RV-A' ? outcome : 'accepted', checked_scope: PRIVATE,
    evidence: [{ source_segment_ids: ['SRC0001'], targets: [target], note: '合成来源与镜头字段依据' }],
    findings: rule_id === 'RV-A' && outcome !== 'accepted' ? [{ finding_id: prior ? 'NEW-F01' : 'OLD-F01', prior_finding_id: prior, rule_id, targets: [target], source_segment_ids: ['SRC0001'],
      problem: '合成运镜问题', impact: '合成影响', repair_scope: [target], preserve: [{ shot_id: 'S1', field: 'visual' }], recheck_condition: '复核原镜头运镜与原来源' }] : [] }));
}
function firstReport(outcome = 'rejected') { return { binding: clone(firstBinding), rules: makeRules(outcome), overall_verdict: outcome }; }
function makeRepair(report) {
  const fields = ['id','duration_ms','visual','camera','audio','depends_on'];
  return { kind: report.overall_verdict === 'unknown' ? 'needs_input' : 'content_repair_ready',
    binding: { source_snapshot_hash: SNAPSHOT, parent_plan_version: 1, parent_plan_hash: digest(parent), review_id: firstBinding.review_id, report_hash: digest(report), rule_version: firstBinding.rule_version, rule_hash: firstBinding.rule_hash, next_plan_version: 2 },
    finding_refs: [{ rule_id: 'RV-A', finding_id: 'OLD-F01' }], repair_targets: [clone(target)], affected_shot_ids: ['S1','S2'],
    preserve_targets: parent.shots.flatMap(shot => fields.filter(field => shot.id !== 'S1' || field !== 'camera').map(field => ({ shot_id: shot.id, field }))),
    invariants: { shot_ids: ['S1','S2'], duration_ms: 15000, ratio: '9:16', source_snapshot_hash: SNAPSHOT, rule_version: firstBinding.rule_version, rule_hash: firstBinding.rule_hash } };
}
function emptyCycle() {
  return { schema_version: 1, evidence_kind: 'synthetic', roles: ['planner','reviewer','repairer','rechecker'], status: 'awaiting_plan', planner_call_count: 0, reviewer_call_count: 0,
    repairer_call_count: 0, rechecker_call_count: 0, max_total_calls: 6, max_reviewer_calls: 1, max_repairer_calls: 1, max_rechecker_calls: 1, paid_call_limit: 0,
    first_review_binding: null, first_report_hash: null, repair_binding: null, recheck_binding: null, recheck_report_hash: null, accepted_plan_version: null,
    parent_plan: null, first_report: null, repair_task: null, recheck_report: null };
}
function task(patch = {}) {
  return { id: A, revision: 1, mode: 'offline_test', scenario: 'revision_accepted', status: 'ready', blocked_reason: null, allowed_actions: ['resume','stop'],
    created_at: source.frozen_at, updated_at: source.frozen_at, source, authorization_status: 'offline_only', execution_enabled: false, quality_status: 'not_reviewed', step_count: 0, repair_count: 0,
    pending_call: null, plan_version: 0, client_request_key: 'original-cycle-create', limits: { max_steps: 6, max_repairs: 2, max_output_bytes: 65536, max_context_bytes: 1048576 },
    plan: null, plan_versions: [], revision_cycle: emptyCycle(), ...patch };
}
function completed(scenario = 'revision_accepted') {
  const first = firstReport(scenario === 'revision_needs_input' ? 'unknown' : 'rejected'), repair = makeRepair(first);
  const value = task({ scenario, revision: 14, status: 'blocked', allowed_actions: [], step_count: 3, plan_version: 1, plan: clone(parent),
    plan_versions: [{ version: 1, plan_hash: digest(parent), created_at: source.frozen_at }] });
  const c = value.revision_cycle;
  Object.assign(c, { planner_call_count: 2, reviewer_call_count: 1, first_review_binding: clone(firstBinding), first_report_hash: digest(first), parent_plan: clone(parent), first_report: first, repair_task: repair });
  if (scenario === 'revision_needs_input') { c.status = 'needs_input'; value.blocked_reason = 'REVIEW_NEEDS_INPUT'; return value; }
  c.repairer_call_count = 1; value.step_count = 4;
  c.repair_binding = { task_id: A, run_id: A, intent_id: REPAIR_OP, source_snapshot_hash: SNAPSHOT, parent_plan_version: 1, parent_plan_hash: digest(parent), review_id: firstBinding.review_id,
    report_hash: digest(first), repair_task_hash: digest(repair), next_plan_version: 2 };
  if (scenario === 'revision_invalid') { c.status = 'repair_invalid'; value.blocked_reason = 'REPAIR_INVALID'; return value; }
  if (scenario === 'revision_response_unknown') { c.status = 'repair_response_unknown'; value.status = 'response_unknown'; value.blocked_reason = 'MODEL_RESPONSE_UNCONFIRMED'; value.allowed_actions = ['stop']; value.pending_call = { operation_id: REPAIR_OP, status: 'intent' }; return value; }
  value.plan_version = 2; value.plan = clone(candidate); value.plan_versions.push({ version: 2, plan_hash: digest(candidate), created_at: source.frozen_at });
  c.rechecker_call_count = 1; value.step_count = 5;
  c.recheck_binding = { ...clone(firstBinding), review_id: 'recheck-review', intent_id: RECHECK_OP, plan_version: 2, plan_hash: digest(candidate) };
  if (scenario === 'recheck_invalid') { c.status = 'recheck_invalid'; value.blocked_reason = 'RECHECK_INVALID'; return value; }
  if (scenario === 'recheck_response_unknown') { c.status = 'recheck_response_unknown'; value.status = 'response_unknown'; value.blocked_reason = 'MODEL_RESPONSE_UNCONFIRMED'; value.allowed_actions = ['stop']; value.pending_call = { operation_id: RECHECK_OP, status: 'intent' }; return value; }
  const outcome = scenario === 'revision_rejected' ? 'rejected' : scenario === 'recheck_unknown' ? 'unknown' : 'accepted';
  c.status = outcome;
  c.recheck_report = { binding: clone(c.recheck_binding), parent_review: { review_id: firstBinding.review_id, plan_version: 1, plan_hash: digest(parent), report_hash: digest(first) },
    resolutions: [{ prior_finding_id: 'OLD-F01', rule_id: 'RV-A', status: outcome === 'accepted' ? 'resolved' : outcome === 'rejected' ? 'unresolved' : 'unknown', source_segment_ids: ['SRC0001'], targets: [clone(target)], note: '合成逐问题复核结论' }],
    rules: makeRules(outcome, 'OLD-F01'), overall_verdict: outcome };
  c.recheck_report_hash = digest(c.recheck_report);
  if (outcome === 'accepted') { c.accepted_plan_version = 2; value.status = 'plan_ready'; value.blocked_reason = null; }
  else value.blocked_reason = outcome === 'rejected' ? 'RECHECK_REQUIRES_REPAIR' : 'RECHECK_NEEDS_INPUT';
  return value;
}
function storage() { const values = new Map(); return { values, getItem: key => values.get(key) ?? null, setItem: (key,value) => values.set(key,value), removeItem: key => values.delete(key) }; }
const gate = () => ({ document: { id: BRIEF, revision: 1, is_current: true }, dirty: false, saving: false, loading: false, pending: null, conflictDetected: false });
const events = () => Response.json({ events: [], next_sequence: 0, has_more: false });
const write = value => Response.json({ task: value, replayed: false, applied_revision: value.revision });
const settle = () => new Promise(resolve => setImmediate(resolve));

test('all eight opt-in scenarios decode their truthful versions and terminal outcomes', async t => {
  for (const scenario of cycleModule.PLANNER_REVISION_SCENARIOS) {
    assert.equal(api.readPlannerDetail(task({ scenario })).revision_cycle.status, 'awaiting_plan');
    const value = completed(scenario), result = api.readPlannerDetail(value);
    assert.equal(result.quality_status, 'not_reviewed'); assert.equal(result.execution_enabled, false);
    assert.equal(result.repair_count, 0); assert.ok(result.step_count <= 6);
    t.mock.method(globalThis, 'fetch', async () => Response.json({ task: value }));
    assert.equal((await api.briefPlannerApi.get(A)).revision_cycle.status, value.revision_cycle.status);
  }
});

test('revision metadata stays exclusive to new scenarios; old D23 first-review remains strict', () => {
  const legacy = task({ scenario: 'success' }); delete legacy.revision_cycle;
  assert.equal('revision_cycle' in api.readPlannerDetail(legacy), false);
  for (const patch of [{ scenario: 'success' }, { mode: 'normal', scenario: null, authorization_status: 'not_authorized' }, { scenario: 'review_accepted' }]) assert.throws(() => api.readPlannerDetail(task(patch)));
  assert.throws(() => api.readPlannerDetail(task({ collaboration: {} })));
  const missing = task(); delete missing.revision_cycle; assert.throws(() => api.readPlannerDetail(missing));
  const invalid = completed(); invalid.revision_cycle.first_report.rules[3].findings[0].prior_finding_id = 'OLD-F01';
  assert.throws(() => api.readPlannerDetail(invalid));
});

test('summary and controller memory omit parent plans, reports and repair proposal bodies', async t => {
  const value = completed(), summary = api.readPlannerSummary(value);
  for (const field of ['parent_plan','first_report','repair_task','recheck_report']) assert.equal(field in summary.revision_cycle, false);
  assert.equal(JSON.stringify(summary).includes(PRIVATE), false);
  const s = storage(), c = new control.BriefPlannerController(() => s);
  t.mock.method(globalThis, 'fetch', async url => url.includes('/events?') ? events() : Response.json({ task: value }));
  await c.open(A);
  assert.equal(JSON.stringify(c.getSnapshot().tasks).includes(PRIVATE), false);
  assert.equal(JSON.stringify([...s.values]).includes(PRIVATE), false);
});

test('current acceptance, caps and all three operation bindings cannot borrow parent facts', () => {
  const mutations = [
    c => { c.accepted_plan_version = 1; }, c => { c.paid_call_limit = 1; }, c => { c.max_total_calls = 7; }, c => { c.repairer_call_count = 0; },
    c => { c.rechecker_call_count = 2; }, c => { c.repair_binding.intent_id = FIRST_OP; }, c => { c.repair_binding.report_hash = '0'.repeat(64); },
    c => { c.repair_binding.parent_plan_hash = '0'.repeat(64); }, c => { c.repair_binding.next_plan_version = 3; },
    c => { c.first_review_binding.plan_version = 2; }, c => { c.recheck_binding.plan_version = 1; },
    c => { c.recheck_binding.intent_id = REPAIR_OP; }, c => { c.recheck_binding.review_id = c.first_review_binding.review_id; },
  ];
  for (const mutate of mutations) { const value = completed(); mutate(value.revision_cycle); assert.throws(() => api.readPlannerDetail(value)); }
});

test('v2 must contain a real permitted field change and preserve ordered members and all other fields', () => {
  const checked = api.readPlannerDetail(completed());
  assert.deepEqual(cycleModule.plannerRevisionChanges(checked.revision_cycle.parent_plan, checked.plan), [{ shot_id: 'S1', field: 'camera', before: '原始合成运镜', after: '修订合成运镜 🎬' }]);
  for (const mutate of [
    value => { value.plan = clone(parent); },
    value => { value.plan.shots[0].visual = '范围外新增事实'; },
    value => { value.plan.shots[1].camera = '依赖后继没有编辑权'; },
    value => { value.plan.shots.reverse(); },
    value => { value.revision_cycle.repair_task.repair_targets.push({ shot_id: 'S2', field: 'camera' }); },
    value => { value.plan_versions[0].plan_hash = '0'.repeat(64); },
  ]) { const value = completed(); mutate(value); assert.throws(() => api.readPlannerDetail(value)); }
});

test('each old finding requires exactly one resolution retaining its original source and shot location', () => {
  const mutations = [
    r => { r.resolutions = []; }, r => { r.resolutions.push(clone(r.resolutions[0])); }, r => { r.resolutions[0].prior_finding_id = 'invented'; },
    r => { r.resolutions[0].source_segment_ids = ['SRC0002']; }, r => { r.resolutions[0].targets = [{ shot_id: 'S2', field: 'camera' }]; },
    r => { r.resolutions[0].rule_id = 'RV-F'; }, r => { r.parent_review.plan_version = 2; }, r => { r.parent_review.report_hash = '0'.repeat(64); },
    r => { r.rules.pop(); }, r => { r.overall_verdict = 'rejected'; },
  ];
  for (const mutate of mutations) { const value = completed(); mutate(value.revision_cycle.recheck_report); assert.throws(() => api.readPlannerDetail(value)); }
});

test('unresolved and unknown resolutions need same-rule new findings; resolved findings cannot remain referenced', () => {
  for (const scenario of ['revision_rejected','recheck_unknown']) {
    const checked = api.readPlannerDetail(completed(scenario));
    assert.equal(checked.revision_cycle.recheck_report.rules[3].findings[0].prior_finding_id, 'OLD-F01');
    const missing = completed(scenario); missing.revision_cycle.recheck_report.rules[3].findings[0].prior_finding_id = null;
    assert.throws(() => api.readPlannerDetail(missing));
    const wrong = completed(scenario); wrong.revision_cycle.recheck_report.resolutions[0].status = 'resolved';
    assert.throws(() => api.readPlannerDetail(wrong));
    const invented = completed(scenario); invented.revision_cycle.recheck_report.rules[3].findings[0].prior_finding_id = 'invented';
    assert.throws(() => api.readPlannerDetail(invented));
  }
});

test('a genuinely accepted first review accepts v1 without manufacturing v2 or repair calls', async t => {
  const report = firstReport('accepted'), value = completed('revision_needs_input'), c = value.revision_cycle;
  Object.assign(c, { status: 'accepted', first_report: report, first_report_hash: digest(report), repair_task: null, accepted_plan_version: 1 });
  value.status = 'plan_ready'; value.blocked_reason = null;
  t.mock.method(globalThis, 'fetch', async () => Response.json({ task: value }));
  const checked = await api.briefPlannerApi.get(A);
  assert.equal(checked.plan_version, 1); assert.equal(checked.step_count, 3); assert.equal(checked.revision_cycle.repairer_call_count, 0);
  assert.equal(checked.revision_cycle.recheck_report, null);
});

test('async API independently verifies parent/current plans, first report, proposal and recheck digests', async t => {
  let value = completed();
  t.mock.method(globalThis, 'fetch', async () => Response.json({ task: value }));
  await api.briefPlannerApi.get(A);
  for (const mutate of [
    v => { v.revision_cycle.parent_plan.shots[0].camera = '未更新父摘要'; },
    v => { v.plan.shots[0].camera = '未更新当前摘要'; },
    v => { v.revision_cycle.first_report.rules[0].checked_scope = '未更新首审摘要'; },
    v => { v.revision_cycle.repair_binding.repair_task_hash = '0'.repeat(64); },
    v => { v.revision_cycle.recheck_report.rules[0].checked_scope = '未更新复核摘要'; },
  ]) { value = completed(); mutate(value); await assert.rejects(api.briefPlannerApi.get(A), error => error.code === 'INVALID_RESPONSE'); }
});

test('one resume returns the entire repair/recheck chain through the original API with no extra start', async t => {
  const s = storage(), c = new control.BriefPlannerController(() => s), posts = []; let createdKey;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (options.method === 'POST') {
      const body = JSON.parse(options.body); posts.push([url,body]);
      if (url === '/api/planner/offline-tasks') { createdKey = body.client_request_key; return write(task({ client_request_key: createdKey })); }
      return write(completed());
    }
    return events();
  });
  await c.create(gate, 'offline_test', 'revision_accepted'); assert.equal(posts.length, 1);
  await c.action('resume');
  assert.equal(posts.length, 2); assert.equal(posts[1][0], `/api/planner/tasks/${A}/actions`);
  assert.equal(posts[1][1].expected_revision, 1); assert.equal(c.getSnapshot().task.plan_version, 2); assert.equal(c.getSnapshot().pending, null);
  assert.equal(c.getSnapshot().task.revision_cycle.accepted_plan_version, 2);
});

test('unknown Repairer/Rechecker intents retain counts and never allow a new resume request', async t => {
  for (const scenario of ['revision_response_unknown','recheck_response_unknown']) {
    const c = new control.BriefPlannerController(() => storage()), value = completed(scenario); let posts = 0;
    t.mock.method(globalThis, 'fetch', async (url, opts) => { if (opts.method === 'POST') posts++; return url.includes('/events?') ? events() : Response.json({ task: value }); });
    await c.open(A); await c.action('resume');
    assert.equal(posts, 0); assert.equal(c.getSnapshot().task.step_count, value.step_count);
    const wrong = clone(value); wrong.pending_call.operation_id = B; assert.throws(() => api.readPlannerDetail(wrong));
  }
});

test('late v2 reports after all async hashes cannot overwrite a newly created selection', async t => {
  const s = storage(), c = new control.BriefPlannerController(() => s); let delay = false, release;
  const pending = new Promise(resolve => { release = resolve; });
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (options.method === 'POST') { const body = JSON.parse(options.body); return write(task({ id: B, client_request_key: body.client_request_key })); }
    if (url.includes('/events?')) return events();
    if (delay) { await pending; return Response.json({ task: completed() }); }
    return Response.json({ task: task() });
  });
  await c.open(A); delay = true; const old = c.refresh(); await settle();
  await c.create(gate, 'offline_test', 'revision_accepted'); release(); await old;
  assert.equal(c.getSnapshot().task.id, B); assert.equal(c.getSnapshot().task.revision_cycle.recheck_report, null);
  assert.equal(s.getItem(c.selectionKey), B); assert.equal(c.getSnapshot().pending, null);
});
