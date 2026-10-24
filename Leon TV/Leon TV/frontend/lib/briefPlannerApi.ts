import type { PlannerActionRequest, PlannerCollaborationDetail, PlannerCollaborationSummary, PlannerCreate, PlannerDetail, PlannerEvent, PlannerFrozenBrief, PlannerOfflineCreate, PlannerPlan, PlannerRepairTask, PlannerReviewBinding, PlannerReviewFinding, PlannerReviewReport, PlannerReviewRuleId, PlannerReviewScenario, PlannerReviewTarget, PlannerReviewVerdict, PlannerScenario, PlannerSource, PlannerSummary, PlannerWrite } from './briefPlannerTypes';
import { PLANNER_REVISION_SCENARIOS, PlannerRevisionCycleReader } from './plannerRevisionCycle';

export class BriefPlannerError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
const id = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{32}$/.test(v);
const hash = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const count = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const positive = (v: unknown): v is number => count(v) && v >= 1;
const key = (v: unknown): v is string => typeof v === 'string' && /^[A-Za-z0-9_-]{8,128}$/.test(v);
const text = (v: unknown): v is string => typeof v === 'string';
export const PLANNER_REVIEW_SCENARIOS: PlannerReviewScenario[] = ['review_accepted', 'review_rejected', 'review_unknown', 'review_response_unknown', 'review_invalid'];
export const PLANNER_SCENARIOS: PlannerScenario[] = ['success', 'repair', 'invalid_tool', 'step_limit', 'response_unknown', ...PLANNER_REVIEW_SCENARIOS, ...PLANNER_REVISION_SCENARIOS];
export const PLANNER_REVIEW_RULE_HASH = 'd05b20cac24da8fe9ec81141733b44f57fb4a39b29f868c35e0faa0bfb26535d';
const RULE_IDS: PlannerReviewRuleId[] = ['RV-F', 'RV-T', 'RV-C', 'RV-A', 'RV-G'];
const SHOT_FIELDS = ['id', 'duration_ms', 'visual', 'camera', 'audio', 'depends_on'] as const;
function invalid(): never { throw new BriefPlannerError(0, 'INVALID_RESPONSE', '规划任务响应与当前合同不一致，请保留原请求并重新查询。'); }
function object(v: unknown): Record<string, unknown> { if (!v || typeof v !== 'object' || Array.isArray(v)) return invalid(); return v as Record<string, unknown>; }
function exact(d: Record<string, unknown>, names: string[]) { if (Object.keys(d).sort().join('|') !== [...names].sort().join('|')) return invalid(); }
function utf8Text(v: unknown): v is string {
  if (!text(v)) return false;
  return Array.from(v).every(char => { const code = char.codePointAt(0)!; return code < 0xd800 || code > 0xdfff; });
}
function reviewText(v: unknown): string { if (!utf8Text(v) || !v.trim() || Array.from(v).length > 2000) return invalid(); return v; }
function identifier(v: unknown): string { if (!text(v) || !/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/.test(v)) return invalid(); return v; }
function verdict(v: unknown): PlannerReviewVerdict { if (v !== 'accepted' && v !== 'rejected' && v !== 'unknown') return invalid(); return v; }
function ruleId(v: unknown): PlannerReviewRuleId { if (!RULE_IDS.includes(v as PlannerReviewRuleId)) return invalid(); return v as PlannerReviewRuleId; }
function readBinding(value: unknown, task: Pick<PlannerSummary, 'id' | 'source' | 'plan_version'>): PlannerReviewBinding {
  const b = object(value);
  exact(b, ['review_id','run_id','intent_id','task_id','plan_version','plan_hash','source_snapshot_hash','rule_version','rule_hash']);
  if (b.run_id !== task.id || b.task_id !== task.id || !positive(b.plan_version) || b.plan_version !== task.plan_version || !hash(b.plan_hash) ||
    b.source_snapshot_hash !== task.source.snapshot_hash || b.rule_version !== 'D15-RV-v0.2' || b.rule_hash !== PLANNER_REVIEW_RULE_HASH) return invalid();
  return { review_id: identifier(b.review_id), run_id: task.id, intent_id: identifier(b.intent_id), task_id: task.id, plan_version: b.plan_version,
    plan_hash: b.plan_hash, source_snapshot_hash: task.source.snapshot_hash, rule_version: 'D15-RV-v0.2', rule_hash: PLANNER_REVIEW_RULE_HASH };
}
function readCollaboration(value: unknown, task: PlannerSummary): PlannerCollaborationSummary {
  const c = object(value);
  if (c.schema_version !== 1 || c.evidence_kind !== 'synthetic' || !Array.isArray(c.roles) || c.roles.length !== 2 || c.roles[0] !== 'planner' || c.roles[1] !== 'reviewer' ||
    !text(c.status) || !['awaiting_plan','review_ready','review_running','accepted','rejected','unknown','response_unknown','invalid'].includes(c.status) ||
    !count(c.planner_call_count) || c.reviewer_call_count !== 0 && c.reviewer_call_count !== 1 || c.max_total_calls !== 6 || c.max_reviewer_calls !== 1 || c.paid_call_limit !== 0 ||
    c.planner_call_count + c.reviewer_call_count !== task.step_count || task.step_count > 6 || !(c.report_hash === null || hash(c.report_hash))) return invalid();
  const binding = c.binding === null ? null : readBinding(c.binding, task);
  if (c.reviewer_call_count === 1 && !binding || c.status !== 'awaiting_plan' && task.plan_version < 1 ||
    ['review_running','accepted','rejected','unknown','response_unknown','invalid'].includes(String(c.status)) && (c.reviewer_call_count !== 1 || !binding) ||
    ['accepted','rejected','unknown'].includes(String(c.status)) !== (c.report_hash !== null)) return invalid();
  if (c.reviewer_call_count === 1 && task.pending_call && task.pending_call.operation_id !== binding?.intent_id ||
    c.status === 'response_unknown' && task.pending_call?.status !== 'intent') return invalid();
  return { schema_version: 1, evidence_kind: 'synthetic', roles: ['planner','reviewer'], status: c.status as PlannerCollaborationSummary['status'],
    planner_call_count: c.planner_call_count, reviewer_call_count: c.reviewer_call_count, max_total_calls: 6, max_reviewer_calls: 1, paid_call_limit: 0,
    binding, report_hash: c.report_hash as string | null };
}
function segments(value: unknown): string[] {
  if (!Array.isArray(value) || !value.length || value.length > 250 || !value.every(v => text(v) && /^SRC\d{4}$/.test(v) && Number(v.slice(3)) >= 1 && Number(v.slice(3)) <= 250)) return invalid();
  return [...value];
}
function targets(value: unknown, plan: PlannerPlan, min = 0): PlannerReviewTarget[] {
  if (!Array.isArray(value) || value.length < min || value.length > 128) return invalid();
  return value.map(v => {
    const t = object(v); exact(t, ['shot_id','field']);
    if (!(t.shot_id === '*' && t.field === '$' || plan.shots.some(s => s.id === t.shot_id) && SHOT_FIELDS.includes(t.field as typeof SHOT_FIELDS[number]))) return invalid();
    return { shot_id: t.shot_id as string, field: t.field as PlannerReviewTarget['field'] };
  });
}
function readReport(value: unknown, binding: PlannerReviewBinding, plan: PlannerPlan): PlannerReviewReport {
  const r = object(value); exact(r, ['binding','rules','overall_verdict']);
  if (pythonContractJson(r.binding) !== pythonContractJson(binding) || !Array.isArray(r.rules) || r.rules.length !== 5) return invalid();
  const findingIds = new Set<string>();
  const rules = r.rules.map(value => {
    const rule = object(value); exact(rule, ['rule_id','verdict','checked_scope','evidence','findings']);
    const rule_id = ruleId(rule.rule_id), result = verdict(rule.verdict);
    if (!Array.isArray(rule.evidence) || !rule.evidence.length || rule.evidence.length > 64 || !Array.isArray(rule.findings) || rule.findings.length > 64 || result !== 'accepted' && !rule.findings.length) return invalid();
    const evidence = rule.evidence.map(value => { const e = object(value); exact(e, ['source_segment_ids','targets','note']);
      return { source_segment_ids: segments(e.source_segment_ids), targets: targets(e.targets, plan, 1), note: reviewText(e.note) }; });
    const findings: PlannerReviewFinding[] = rule.findings.map(value => {
      const f = object(value); exact(f, ['finding_id','prior_finding_id','rule_id','targets','source_segment_ids','problem','impact','repair_scope','preserve','recheck_condition']);
      const finding_id = identifier(f.finding_id);
      if (f.prior_finding_id !== null || f.rule_id !== rule_id || findingIds.has(finding_id)) return invalid();
      findingIds.add(finding_id);
      return { finding_id, prior_finding_id: null, rule_id, targets: targets(f.targets, plan, 1), source_segment_ids: segments(f.source_segment_ids),
        problem: reviewText(f.problem), impact: reviewText(f.impact), repair_scope: targets(f.repair_scope, plan), preserve: targets(f.preserve, plan), recheck_condition: reviewText(f.recheck_condition) };
    });
    return { rule_id, verdict: result, checked_scope: reviewText(rule.checked_scope), evidence, findings };
  });
  const aggregate = rules.some(r => r.verdict === 'rejected') ? 'rejected' : rules.some(r => r.verdict === 'unknown') ? 'unknown' : 'accepted';
  if (new Set(rules.map(r => r.rule_id)).size !== 5 || verdict(r.overall_verdict) !== aggregate) return invalid();
  const report = { binding, rules, overall_verdict: aggregate } as PlannerReviewReport;
  if (new TextEncoder().encode(JSON.stringify(report)).byteLength > 65536) return invalid();
  return report;
}
function expectedRepair(report: PlannerReviewReport, reportHash: string, plan: PlannerPlan): PlannerRepairTask {
  const b = report.binding, shotIds = plan.shots.map(s => s.id);
  const findings = report.rules.filter(r => r.verdict !== 'accepted').flatMap(r => r.findings).sort((a,b) => {
    const left = `${a.rule_id}|${a.finding_id}`, right = `${b.rule_id}|${b.finding_id}`; return left < right ? -1 : left > right ? 1 : 0;
  });
  const pairs = (items: PlannerReviewTarget[]) => new Set(items.flatMap(t => t.shot_id === '*' ? shotIds.flatMap(id => SHOT_FIELDS.map(field => `${id}|${field}`)) : [`${t.shot_id}|${t.field}`]));
  const scope = pairs(findings.flatMap(f => f.repair_scope)), keep = pairs(findings.flatMap(f => f.preserve));
  shotIds.forEach(id => keep.add(`${id}|id`));
  const editable = new Set([...scope].filter(p => !keep.has(p)));
  const all = shotIds.flatMap(shot_id => SHOT_FIELDS.map(field => ({ shot_id, field })));
  const affected = new Set(all.filter(t => editable.has(`${t.shot_id}|${t.field}`)).map(t => t.shot_id));
  let changed = true;
  while (changed) { changed = false; for (const shot of plan.shots) if (!affected.has(shot.id) && shot.depends_on.some(id => affected.has(id))) { affected.add(shot.id); changed = true; } }
  return { kind: !editable.size || report.rules.some(r => r.verdict === 'unknown') ? 'needs_input' : 'content_repair_ready',
    binding: { source_snapshot_hash: b.source_snapshot_hash, parent_plan_version: b.plan_version, parent_plan_hash: b.plan_hash, review_id: b.review_id,
      report_hash: reportHash, rule_version: b.rule_version, rule_hash: b.rule_hash, next_plan_version: b.plan_version + 1 },
    finding_refs: findings.map(f => ({ rule_id: f.rule_id, finding_id: f.finding_id })), repair_targets: all.filter(t => editable.has(`${t.shot_id}|${t.field}`)),
    affected_shot_ids: shotIds.filter(id => affected.has(id)), preserve_targets: all.filter(t => !editable.has(`${t.shot_id}|${t.field}`)),
    invariants: { shot_ids: shotIds, duration_ms: 15000, ratio: '9:16', source_snapshot_hash: b.source_snapshot_hash, rule_version: b.rule_version, rule_hash: b.rule_hash } };
}
function readCollaborationDetail(value: unknown, summary: PlannerCollaborationSummary, task: PlannerDetail): PlannerCollaborationDetail {
  const c = object(value);
  exact(c, ['schema_version','evidence_kind','roles','status','planner_call_count','reviewer_call_count','max_total_calls','max_reviewer_calls','paid_call_limit','binding','report_hash','report','repair_task']);
  const b = summary.binding;
  if (b && (!task.plan || task.plan_versions.find(v => v.version === task.plan_version)?.plan_hash !== b.plan_hash)) return invalid();
  let report: PlannerReviewReport | null = null, repair: PlannerRepairTask | null = null;
  if (c.report !== null) {
    if (!b || !task.plan || !summary.report_hash) return invalid();
    report = readReport(c.report, b, task.plan);
    if (summary.status !== report.overall_verdict) return invalid();
    if (report.overall_verdict !== 'accepted') {
      repair = expectedRepair(report, summary.report_hash, task.plan);
      if (pythonContractJson(c.repair_task) !== pythonContractJson(repair)) return invalid();
    } else if (c.repair_task !== null) return invalid();
  } else if (summary.report_hash !== null || c.repair_task !== null || ['accepted','rejected','unknown'].includes(summary.status)) return invalid();
  return { ...summary, report, repair_task: repair };
}
/** Python creator.digest uses sorted keys and its default comma/colon spaces. */
export function pythonContractJson(value: unknown): string {
  if (value === null || typeof value === 'boolean') return String(value);
  if (typeof value === 'string') { if (!utf8Text(value)) return invalid(); return JSON.stringify(value); }
  if (typeof value === 'number') { if (!Number.isSafeInteger(value)) return invalid(); return String(value); }
  if (Array.isArray(value)) return `[${value.map(pythonContractJson).join(', ')}]`;
  const d = object(value);
  if (Object.getPrototypeOf(d) !== Object.prototype && Object.getPrototypeOf(d) !== null) return invalid();
  return `{${Object.keys(d).sort().map(key => `${JSON.stringify(key)}: ${pythonContractJson(d[key])}`).join(', ')}}`;
}
export async function plannerContractDigest(value: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(pythonContractJson(value));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}
const revisionReader = new PlannerRevisionCycleReader({ invalid, binding: readBinding, report: readReport, repair: expectedRepair,
  plan: readPlannerPlan, json: pythonContractJson, digest: plannerContractDigest });
export function readPlannerSource(value: unknown): PlannerSource {
  const d = object(value), p = object(d.production);
  if (!id(d.brief_id) || !positive(d.brief_revision) || !positive(d.source_version) || !hash(d.source_hash) || !hash(d.snapshot_hash) || !text(d.title) || !text(d.frozen_at) ||
    p.duration_seconds !== 15 || p.ratio !== '9:16' || p.style !== 'realistic_cinematic' || d.currency !== 'CNY' ||
    !(d.budget_cny === null || text(d.budget_cny) && /^\d+\.\d{2}$/.test(d.budget_cny))) return invalid();
  return { brief_id: d.brief_id, brief_revision: d.brief_revision, source_version: d.source_version, source_hash: d.source_hash, snapshot_hash: d.snapshot_hash,
    title: d.title, production: { duration_seconds: 15, ratio: '9:16', style: 'realistic_cinematic' }, budget_cny: d.budget_cny as string | null, currency: 'CNY', frozen_at: d.frozen_at };
}
export function readPlannerSummary(value: unknown): PlannerSummary {
  const d = object(value);
  if (!id(d.id) || !positive(d.revision) || !['normal', 'offline_test'].includes(String(d.mode)) ||
    !(d.mode === 'normal' ? d.scenario === null && d.authorization_status === 'not_authorized' : PLANNER_SCENARIOS.includes(d.scenario as PlannerScenario) && d.authorization_status === 'offline_only') ||
    !['awaiting_authorization','ready','queued','running','stopped','response_unknown','plan_ready','blocked'].includes(String(d.status)) ||
    !(d.blocked_reason === null || text(d.blocked_reason)) || !Array.isArray(d.allowed_actions) || !d.allowed_actions.every(x => x === 'stop' || x === 'resume') ||
    !text(d.created_at) || !text(d.updated_at) || d.execution_enabled !== false || d.quality_status !== 'not_reviewed' ||
    !count(d.step_count) || !count(d.repair_count) || !count(d.plan_version)) return invalid();
  let pending: PlannerSummary['pending_call'] = null;
  if (d.pending_call !== null) { const p = object(d.pending_call); if (!id(p.operation_id) || !['intent','received'].includes(String(p.status))) return invalid(); pending = { operation_id: p.operation_id, status: p.status as 'intent' | 'received' }; }
  const summary: PlannerSummary = { id: d.id, revision: d.revision, mode: d.mode as PlannerSummary['mode'], scenario: d.scenario as PlannerSummary['scenario'], status: d.status as PlannerSummary['status'],
    blocked_reason: d.blocked_reason as string | null, allowed_actions: d.allowed_actions as PlannerSummary['allowed_actions'], created_at: d.created_at, updated_at: d.updated_at,
    source: readPlannerSource(d.source), authorization_status: d.authorization_status as PlannerSummary['authorization_status'], execution_enabled: false, quality_status: 'not_reviewed',
    step_count: d.step_count, repair_count: d.repair_count, pending_call: pending, plan_version: d.plan_version };
  const collaborative = d.mode === 'offline_test' && PLANNER_REVIEW_SCENARIOS.includes(d.scenario as PlannerReviewScenario);
  if (collaborative) summary.collaboration = readCollaboration(d.collaboration, summary);
  else if ('collaboration' in d) return invalid();
  const revision = d.mode === 'offline_test' && PLANNER_REVISION_SCENARIOS.some(scenario => scenario === d.scenario);
  if (revision) summary.revision_cycle = revisionReader.summary(d.revision_cycle, summary);
  else if ('revision_cycle' in d) return invalid();
  return summary;
}
export function readPlannerPlan(value: unknown, snapshot: string): PlannerPlan {
  const p = object(value);
  if (p.source_snapshot_hash !== snapshot || p.duration_ms !== 15000 || p.ratio !== '9:16' || !Array.isArray(p.shots) || !p.shots.length || p.shots.length > 12) return invalid();
  const shots = p.shots.map(value => {
    const s = object(value);
    if (!text(s.id) || !/^[A-Za-z][A-Za-z0-9_-]{0,31}$/.test(s.id) || !positive(s.duration_ms) || s.duration_ms > 15000 || !text(s.visual) || !s.visual.trim() || Array.from(s.visual).length > 2000 ||
      !text(s.camera) || Array.from(s.camera).length > 500 || !text(s.audio) || Array.from(s.audio).length > 500 || !Array.isArray(s.depends_on) || s.depends_on.length > 11 || !s.depends_on.every(text)) return invalid();
    return { id: s.id, duration_ms: s.duration_ms, visual: s.visual, camera: s.camera, audio: s.audio, depends_on: s.depends_on as string[] };
  });
  const ids = new Map(shots.map(s => [s.id, s]));
  if (ids.size !== shots.length || shots.reduce((n, s) => n + s.duration_ms, 0) !== 15000) return invalid();
  const checked = new Set<string>(), visiting = new Set<string>();
  function visit(name: string) {
    if (visiting.has(name) || !ids.has(name)) return invalid();
    if (checked.has(name)) return;
    visiting.add(name); const deps = ids.get(name)!.depends_on;
    if (new Set(deps).size !== deps.length) return invalid();
    deps.forEach(visit); visiting.delete(name); checked.add(name);
  }
  shots.forEach(s => visit(s.id));
  return { source_snapshot_hash: snapshot, duration_ms: 15000, ratio: '9:16', shots };
}
export function readPlannerDetail(value: unknown): PlannerDetail {
  const d = object(value), summary = readPlannerSummary(d), limits = object(d.limits);
  if (!key(d.client_request_key) || limits.max_steps !== 6 || limits.max_repairs !== 2 || limits.max_output_bytes !== 65536 || limits.max_context_bytes !== 1048576 || !Array.isArray(d.plan_versions)) return invalid();
  const versions = d.plan_versions.map(v => { const r = object(v); if (!positive(r.version) || !hash(r.plan_hash) || !text(r.created_at)) return invalid(); return { version: r.version, plan_hash: r.plan_hash, created_at: r.created_at }; });
  const detail: PlannerDetail = { ...summary, collaboration: undefined, revision_cycle: undefined, client_request_key: d.client_request_key, limits: { max_steps: 6, max_repairs: 2, max_output_bytes: 65536, max_context_bytes: 1048576 },
    plan: d.plan === null ? null : readPlannerPlan(d.plan, summary.source.snapshot_hash), plan_versions: versions };
  if (summary.collaboration) detail.collaboration = readCollaborationDetail(d.collaboration, summary.collaboration, detail);
  else delete detail.collaboration;
  if (summary.revision_cycle) detail.revision_cycle = revisionReader.detail(d.revision_cycle, summary.revision_cycle, detail);
  else delete detail.revision_cycle;
  return detail;
}
async function checkedDetail(value: unknown): Promise<PlannerDetail> {
  const task = readPlannerDetail(value), c = task.collaboration;
  // These client checks supplement server-owned source, permission and binding checks.
  if (c?.binding && (!task.plan || await plannerContractDigest(task.plan) !== c.binding.plan_hash)) return invalid();
  if (c?.report && await plannerContractDigest(c.report) !== c.report_hash) return invalid();
  await revisionReader.verify(task);
  return task;
}
async function write(data: Record<string, unknown>): Promise<PlannerWrite> {
  if (typeof data.replayed !== 'boolean' || !positive(data.applied_revision)) return invalid();
  return { task: await checkedDetail(data.task), replayed: data.replayed, applied_revision: data.applied_revision };
}
async function request(url: string, method = 'GET', body?: unknown) {
  const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 15000);
  try {
    const response = await fetch(url, { method, cache: 'no-store', signal: abort.signal, ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
    const data: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const d = data && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : null;
      const code = d && typeof d === 'object' && 'code' in d && typeof d.code === 'string' ? d.code : `HTTP_${response.status}`;
      const messages: Record<string, string> = {
        NOT_FOUND: '暂未查到原任务、简报版本或请求键。请保留原键。', INVALID_INPUT: '规划请求不符合当前接口要求，请核对保存版本。',
        KEY_CONFLICT: '原请求键已绑定其他请求，不能换键绕过，请先查回原任务。', REVISION_CONFLICT: '任务已更新，本次动作未提交。请核对最新状态后重新选择。',
        ACTION_NOT_ALLOWED: '任务当前不允许该动作，请刷新后核对。', PRIVATE_STORAGE_UNAVAILABLE: '私有规划服务暂不可用，请保留原请求稍后查询。',
        CORRUPT_STORAGE: '私有规划记录无法校验，已停止读写，请保留原记录。',
        REVIEW_REQUIRES_REPAIR: '合成审阅已提出有限返工范围，当前不会自动改稿或复核。',
        REVIEW_NEEDS_INPUT: '合成审阅缺少必要信息，当前不能判为合格。', REVIEW_INVALID: '合成审阅格式或版本绑定无效，请核对安全记录。',
      };
      throw new BriefPlannerError(response.status, code, messages[code] || `规划请求失败（HTTP ${response.status}），请查询原请求。`);
    }
    return object(data);
  } catch (error) {
    if (abort.signal.aborted || error instanceof TypeError) throw new BriefPlannerError(0, 'OUTCOME_UNKNOWN', method === 'GET' ? '规划查询超时或网络中断，请重新查询。' : '规划响应未确认，原请求已保留，请先查回，或按原键重试。');
    throw error;
  } finally { clearTimeout(timer); }
}
const root = '/api/planner';
export const briefPlannerApi = {
  async list(offset = 0) {
    const d = await request(`${root}/tasks?offset=${offset}&limit=50`);
    if (!Array.isArray(d.tasks) || typeof d.has_more !== 'boolean' || !(d.next_offset === null || count(d.next_offset))) return invalid();
    return { tasks: d.tasks.map(readPlannerSummary), next_offset: d.next_offset as number | null, has_more: d.has_more };
  },
  async byKey(value: string) { return readPlannerSummary((await request(`${root}/tasks/by-key/${encodeURIComponent(value)}`)).task); },
  async get(value: string) { return checkedDetail((await request(`${root}/tasks/${encodeURIComponent(value)}`)).task); },
  async source(value: string): Promise<PlannerFrozenBrief> {
    const s = object((await request(`${root}/tasks/${encodeURIComponent(value)}/source`)).source);
    if (!text(s.script) || !text(s.requirements)) return invalid();
    return { ...readPlannerSource(s), script: s.script, requirements: s.requirements };
  },
  async events(value: string, after = 0) {
    const d = await request(`${root}/tasks/${encodeURIComponent(value)}/events?after_sequence=${after}&limit=100`);
    if (!Array.isArray(d.events) || !count(d.next_sequence) || typeof d.has_more !== 'boolean') return invalid();
    const events: PlannerEvent[] = d.events.map(value => {
      const e = object(value);
      if (!positive(e.sequence) || !positive(e.task_revision) || !text(e.type) || !text(e.created_at) || !(e.step === null || count(e.step)) || !(e.code === null || text(e.code))) return invalid();
      return { sequence: e.sequence, type: e.type, task_revision: e.task_revision, created_at: e.created_at, step: e.step as number | null, code: e.code as string | null };
    });
    if (events.some((e, i) => e.sequence !== after + i + 1) || d.next_sequence !== (events.at(-1)?.sequence ?? after)) return invalid();
    return { events, next_sequence: d.next_sequence, has_more: d.has_more };
  },
  async create(input: PlannerCreate) { return write(await request(`${root}/tasks`, 'POST', input)); },
  async createOffline(input: PlannerOfflineCreate) { return write(await request(`${root}/offline-tasks`, 'POST', input)); },
  async action(value: string, input: PlannerActionRequest) { return write(await request(`${root}/tasks/${encodeURIComponent(value)}/actions`, 'POST', input)); },
};
