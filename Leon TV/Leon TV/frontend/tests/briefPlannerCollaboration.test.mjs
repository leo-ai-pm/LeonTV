import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: api } = await loadModule('lib/briefPlannerApi.ts');
const { module: control } = await loadModule('lib/briefPlannerController.ts');
const A = 'a'.repeat(32), B = 'b'.repeat(32), BRIEF = 'c'.repeat(32), SNAPSHOT = 'e'.repeat(64), OP = 'f'.repeat(32);
const PRIVATE = 'SYNTHETIC_REPORT_DETAIL_ONLY';
const production = { duration_seconds: 15, ratio: '9:16', style: 'realistic_cinematic' };
const source = { brief_id: BRIEF, brief_revision: 1, source_version: 1, source_hash: 'd'.repeat(64), snapshot_hash: SNAPSHOT, title: '合成协作简报', production, budget_cny: null, currency: 'CNY', frozen_at: '2026-10-24T00:00:00Z' };
// Independent Node digest over the documented Python JSON wire representation.
const canonical = v => Array.isArray(v) ? `[${v.map(canonical).join(', ')}]` : v && typeof v === 'object' ? `{${Object.keys(v).sort().map(k => `${JSON.stringify(k)}: ${canonical(v[k])}`).join(', ')}}` : JSON.stringify(v);
const digest = v => createHash('sha256').update(canonical(v)).digest('hex');
const plan = { source_snapshot_hash: SNAPSHOT, duration_ms: 15000, ratio: '9:16', shots: [
  { id: 'S1', duration_ms: 6000, visual: '合成画面一 🎬', camera: '', audio: '', depends_on: [] },
  { id: 'S2', duration_ms: 9000, visual: '合成画面二', camera: '', audio: '', depends_on: ['S1'] },
] };
const planHash = digest(plan);
const binding = { review_id: 'review-1', run_id: A, intent_id: OP, task_id: A, plan_version: 1, plan_hash: planHash, source_snapshot_hash: SNAPSHOT, rule_version: 'D15-RV-v0.2', rule_hash: api.PLANNER_REVIEW_RULE_HASH };
const ruleIds = ['RV-F','RV-T','RV-C','RV-A','RV-G'];
const target = { shot_id: 'S1', field: 'visual' };
const clone = v => structuredClone(v);
function report(outcome = 'accepted') {
  return { binding, overall_verdict: outcome, rules: ruleIds.map((rule_id, i) => ({ rule_id, verdict: i === 0 ? outcome : 'accepted', checked_scope: PRIVATE,
    evidence: [{ source_segment_ids: ['SRC0001'], targets: [target], note: '合成来源与镜头定位依据' }],
    findings: i === 0 && outcome !== 'accepted' ? [{ finding_id: 'finding-1', prior_finding_id: null, rule_id, targets: [target], source_segment_ids: ['SRC0001'],
      problem: '合成定位问题', impact: '合成影响', repair_scope: [target], preserve: [], recheck_condition: '核对原来源与修订字段' }] : [] })) };
}
function repair(r) {
  const fields = ['id','duration_ms','visual','camera','audio','depends_on'];
  return { kind: r.overall_verdict === 'unknown' ? 'needs_input' : 'content_repair_ready',
    binding: { source_snapshot_hash: SNAPSHOT, parent_plan_version: 1, parent_plan_hash: planHash, review_id: binding.review_id, report_hash: digest(r), rule_version: binding.rule_version, rule_hash: binding.rule_hash, next_plan_version: 2 },
    finding_refs: [{ rule_id: 'RV-F', finding_id: 'finding-1' }], repair_targets: [target], affected_shot_ids: ['S1','S2'],
    preserve_targets: plan.shots.flatMap(s => fields.filter(field => s.id !== 'S1' || field !== 'visual').map(field => ({ shot_id: s.id, field }))),
    invariants: { shot_ids: ['S1','S2'], duration_ms: 15000, ratio: '9:16', source_snapshot_hash: SNAPSHOT, rule_version: binding.rule_version, rule_hash: binding.rule_hash } };
}
function task(patch = {}) {
  return { id: A, revision: 1, mode: 'offline_test', scenario: 'review_accepted', status: 'ready', blocked_reason: null, allowed_actions: ['resume','stop'],
    created_at: source.frozen_at, updated_at: source.frozen_at, source, authorization_status: 'offline_only', execution_enabled: false, quality_status: 'not_reviewed',
    step_count: 0, repair_count: 0, pending_call: null, plan_version: 0, client_request_key: 'original-create-key',
    limits: { max_steps: 6, max_repairs: 2, max_output_bytes: 65536, max_context_bytes: 1048576 }, plan: null, plan_versions: [],
    collaboration: { schema_version: 1, evidence_kind: 'synthetic', roles: ['planner','reviewer'], status: 'awaiting_plan', planner_call_count: 0, reviewer_call_count: 0,
      max_total_calls: 6, max_reviewer_calls: 1, paid_call_limit: 0, binding: null, report_hash: null, report: null, repair_task: null }, ...patch };
}
function reviewed(outcome = 'accepted') {
  const r = report(outcome);
  return task({ revision: 8, scenario: `review_${outcome}`, status: outcome === 'accepted' ? 'plan_ready' : 'blocked', blocked_reason: outcome === 'accepted' ? null : outcome === 'rejected' ? 'REVIEW_REQUIRES_REPAIR' : 'REVIEW_NEEDS_INPUT', allowed_actions: [],
    step_count: 3, plan_version: 1, plan, plan_versions: [{ version: 1, plan_hash: planHash, created_at: source.frozen_at }],
    collaboration: { ...task().collaboration, status: outcome, planner_call_count: 2, reviewer_call_count: 1, binding, report_hash: digest(r), report: r, repair_task: outcome === 'accepted' ? null : repair(r) } });
}
function storage() { const values = new Map(); return { values, getItem: k => values.get(k) ?? null, setItem: (k,v) => values.set(k,v), removeItem: k => values.delete(k) }; }
const gate = () => ({ document: { id: BRIEF, revision: 1, is_current: true }, dirty: false, saving: false, loading: false, pending: null, conflictDetected: false });
const events = () => Response.json({ events: [], next_sequence: 0, has_more: false });
const write = value => Response.json({ task: value, replayed: false, applied_revision: value.revision });
const settle = () => new Promise(r => setImmediate(r));

test('digest matches Python known vectors including default spaces, Unicode, emoji, escapes and nested values', async () => {
  const vectors = [
    [{ a: 1, b: 'ASCII' }, '2fbdfc6068944a00f359331dca56aa22ddd23112b729febc639dbf240d6463e9'],
    [{ 中文: '分镜', emoji: '🎬' }, '3bd3cf0b091830794d2b27102192c94d65a7a1aea0e6bd8246663dfeb40a3172'],
    [{ quote: '"\\\n\t\b\f\r', nested: [null,true,false,{ z: ['é','😀'], a: 0 }] }, '469021e1f90fbd0991c501cb54b4fbbda7ca44a17b875eac6c7cfdb8fb506e14'],
  ];
  for (const [value, expected] of vectors) assert.equal(await api.plannerContractDigest(value), expected);
  assert.throws(() => api.pythonContractJson({ unsafe: '\ud800' }));
  assert.throws(() => api.pythonContractJson({ unsafe: NaN }));
});

test('only the five new offline scenarios accept collaboration and keep real quality unreviewed', () => {
  for (const scenario of api.PLANNER_REVIEW_SCENARIOS) assert.equal(api.readPlannerDetail(task({ scenario })).collaboration.evidence_kind, 'synthetic');
  const normal = task({ mode: 'normal', scenario: null, authorization_status: 'not_authorized' });
  assert.throws(() => api.readPlannerDetail(normal));
  const legacy = task({ scenario: 'success' }); delete legacy.collaboration;
  assert.equal('collaboration' in api.readPlannerDetail(legacy), false);
  assert.throws(() => api.readPlannerDetail({ ...legacy, collaboration: task().collaboration }));
  const missing = task(); delete missing.collaboration; assert.throws(() => api.readPlannerDetail(missing));
  assert.throws(() => api.readPlannerDetail(task({ quality_status: 'accepted' })));
});

test('summary projection drops complete reports and rejects changed caps, role counts and bindings', () => {
  const summary = api.readPlannerSummary(reviewed());
  assert.equal('report' in summary.collaboration, false); assert.equal('repair_task' in summary.collaboration, false);
  assert.equal(JSON.stringify(summary).includes(PRIVATE), false);
  for (const patch of [{ roles: ['reviewer','planner'] }, { roles: ['planner|reviewer'] }, { status: ['accepted'] }, { planner_call_count: 3 }, { reviewer_call_count: 2 }, { paid_call_limit: 1 }, { max_total_calls: 7 }, { evidence_kind: 'real' }]) {
    const value = reviewed(); Object.assign(value.collaboration, patch); assert.throws(() => api.readPlannerDetail(value));
  }
  for (const patch of [{ task_id: B }, { run_id: B }, { plan_version: 2 }, { source_snapshot_hash: '0'.repeat(64) }, { rule_hash: '0'.repeat(64) }, { rule_version: 'guessed-version' }]) {
    const value = clone(reviewed()); Object.assign(value.collaboration.binding, patch); assert.throws(() => api.readPlannerDetail(value));
  }
});

test('valid accepted, rejected and unknown reports keep five criteria and exact bounded repair proposals', () => {
  for (const outcome of ['accepted','rejected','unknown']) {
    const value = api.readPlannerDetail(reviewed(outcome));
    assert.equal(value.quality_status, 'not_reviewed'); assert.equal(value.collaboration.report.overall_verdict, outcome);
    assert.equal(value.collaboration.report.rules.length, 5);
    if (outcome === 'accepted') assert.equal(value.collaboration.repair_task, null);
    else {
      assert.equal(value.collaboration.repair_task.kind, outcome === 'unknown' ? 'needs_input' : 'content_repair_ready');
      assert.deepEqual(value.collaboration.repair_task.repair_targets, [target]);
      assert.deepEqual(value.collaboration.repair_task.affected_shot_ids, ['S1','S2']);
    }
  }
});

test('reports reject wrong aggregation, report binding, missing criteria and invalid shot locations', () => {
  const mutations = [
    v => { v.collaboration.report.overall_verdict = 'accepted'; },
    v => { v.collaboration.report.binding.plan_hash = '0'.repeat(64); },
    v => { v.collaboration.report.rules.pop(); },
    v => { v.collaboration.report.rules[0].findings = []; },
    v => { v.collaboration.report.rules[0].evidence[0].targets[0].shot_id = 'UNKNOWN'; },
    v => { v.collaboration.report.rules[0].evidence[0].source_segment_ids = ['SRC0000']; },
    v => { v.collaboration.repair_task.repair_targets.push({ shot_id: 'S2', field: 'visual' }); },
    v => { v.collaboration.repair_task.binding.next_plan_version = 3; },
    v => { v.collaboration.repair_task.kind = 'content_repair_ready'; },
  ];
  for (const mutate of mutations) { const value = clone(reviewed('unknown')); mutate(value); assert.throws(() => api.readPlannerDetail(value)); }
});

test('asynchronous detail reads independently check current plan and complete report SHA256', async t => {
  let current = reviewed();
  t.mock.method(globalThis, 'fetch', async () => Response.json({ task: current }));
  assert.equal((await api.briefPlannerApi.get(A)).collaboration.report.overall_verdict, 'accepted');
  current = clone(reviewed()); current.plan.shots[0].visual = '摘要未更新的改稿';
  await assert.rejects(api.briefPlannerApi.get(A), error => error.code === 'INVALID_RESPONSE');
  current = clone(reviewed()); current.collaboration.report.rules[0].checked_scope = '摘要未更新的报告';
  await assert.rejects(api.briefPlannerApi.get(A), error => error.code === 'INVALID_RESPONSE');
});

test('response unknown keeps original reviewer intent; invalid reports cannot masquerade as content rejection', () => {
  const base = reviewed();
  const unknown = task({ ...base, scenario: 'review_response_unknown', status: 'response_unknown', pending_call: { operation_id: OP, status: 'intent' },
    collaboration: { ...base.collaboration, status: 'response_unknown', report_hash: null, report: null, repair_task: null } });
  assert.equal(api.readPlannerDetail(unknown).collaboration.report, null);
  const wrong = clone(unknown); wrong.pending_call.operation_id = B; assert.throws(() => api.readPlannerDetail(wrong));
  const invalid = task({ ...base, scenario: 'review_invalid', status: 'blocked', blocked_reason: 'REVIEW_INVALID', collaboration: { ...base.collaboration, status: 'invalid', report_hash: null, report: null, repair_task: null } });
  assert.equal(api.readPlannerDetail(invalid).collaboration.report, null);
  invalid.collaboration.report = report('rejected'); assert.throws(() => api.readPlannerDetail(invalid));
});

test('one explicit resume uses old API and receives both roles without another action; lists retain metadata only', async t => {
  const s = storage(), c = new control.BriefPlannerController(() => s), posts = []; let current;
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') {
      const input = JSON.parse(opts.body); posts.push([url, input]);
      if (url === '/api/planner/offline-tasks') { current = task({ client_request_key: input.client_request_key, scenario: input.scenario }); return write(current); }
      current = reviewed('rejected'); return write(current);
    }
    return url.includes('/events?') ? events() : Response.json({ task: current });
  });
  await c.create(gate, 'offline_test', 'review_rejected'); assert.equal(posts.length, 1);
  await c.action('resume'); assert.equal(posts.length, 2);
  assert.equal(posts[1][0], `/api/planner/tasks/${A}/actions`); assert.equal(posts[1][1].expected_revision, 1);
  assert.equal(c.getSnapshot().task.collaboration.report.overall_verdict, 'rejected');
  assert.equal('report' in c.getSnapshot().tasks[0].collaboration, false);
  assert.equal(JSON.stringify([...s.values]).includes(PRIVATE), false);
});

test('lost collaborative resume preserves original action/version through refresh and same-key replay', async t => {
  const s = storage(), c = new control.BriefPlannerController(() => s); let original, writes = 0, current = task();
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') { writes++; original = JSON.parse(opts.body); throw new TypeError('lost'); }
    return url.includes('/events?') ? events() : Response.json({ task: current });
  });
  await c.open(A); await c.action('resume'); const raw = s.getItem(c.storageKey);
  current = reviewed(); await c.recover(false);
  assert.equal(s.getItem(c.storageKey), raw); assert.equal(c.getSnapshot().task.revision, 8); assert.equal(writes, 1);
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') { writes++; assert.deepEqual(JSON.parse(opts.body), original); return write(current); }
    return events();
  });
  await c.recover(true); assert.equal(c.getSnapshot().pending, null); assert.equal(writes, 2);
});

test('a delayed report cannot cross task selection or replace the current collaboration', async t => {
  const c = new control.BriefPlannerController(() => storage()); let release, pending = false;
  const wait = new Promise(r => { release = r; });
  t.mock.method(globalThis, 'fetch', async url => {
    if (url.endsWith(A)) { pending = true; await wait; return Response.json({ task: reviewed() }); }
    if (url.includes('/events?')) return events();
    return Response.json({ task: task({ id: B }) });
  });
  const opening = c.open(A); while (!pending) await settle(); await c.open(B); release(); await opening;
  assert.equal(c.getSnapshot().task.id, B); assert.equal(c.getSnapshot().task.collaboration.report, null); assert.deepEqual(c.getSnapshot().events, []);
});

test('a previous-task refresh cannot replace a newly created task after asynchronous report verification', async t => {
  const s = storage(), c = new control.BriefPlannerController(() => s); let refreshing = false, release;
  const delayed = new Promise(r => { release = r; });
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') {
      const body = JSON.parse(opts.body);
      return write(task({ id: B, client_request_key: body.client_request_key }));
    }
    if (url.includes('/events?')) return events();
    if (refreshing) { await delayed; return Response.json({ task: reviewed() }); }
    return Response.json({ task: task() });
  });
  await c.open(A); refreshing = true;
  const oldRefresh = c.refresh(); await settle();
  await c.create(gate, 'offline_test', 'review_accepted');
  assert.equal(c.getSnapshot().task.id, B); assert.equal(c.getSnapshot().pending, null);
  release(); await oldRefresh;
  assert.equal(c.getSnapshot().task.id, B); assert.equal(c.getSnapshot().task.collaboration.report, null);
  assert.equal(s.getItem(c.selectionKey), B); assert.equal(c.getSnapshot().refreshing, false);
});

test('same-key create recovery selects its bound task without a late prior refresh retargeting it', async t => {
  const s = storage(), c = new control.BriefPlannerController(() => s); let sent, refreshing = false, recovering = false, release;
  const delayed = new Promise(r => { release = r; });
  t.mock.method(globalThis, 'fetch', async (url, opts) => {
    if (opts.method === 'POST') { sent = JSON.parse(opts.body); throw new TypeError('lost creation response'); }
    if (url.includes('/events?')) return events();
    if (recovering && url.includes('/by-key/')) return Response.json({ task: task({ id: B }) });
    if (recovering && url.endsWith(B)) return Response.json({ task: task({ id: B, client_request_key: sent.client_request_key }) });
    if (refreshing) { await delayed; return Response.json({ task: reviewed() }); }
    return Response.json({ task: task() });
  });
  await c.open(A); refreshing = true;
  const oldRefresh = c.refresh(); await settle();
  await c.create(gate, 'offline_test', 'review_accepted');
  assert.equal(JSON.parse(s.getItem(c.storageKey)).request.client_request_key, sent.client_request_key);
  recovering = true; await c.recover(false);
  assert.equal(c.getSnapshot().task.id, B); assert.equal(c.getSnapshot().pending, null);
  release(); await oldRefresh;
  assert.equal(c.getSnapshot().task.id, B); assert.equal(s.getItem(c.selectionKey), B);
});
