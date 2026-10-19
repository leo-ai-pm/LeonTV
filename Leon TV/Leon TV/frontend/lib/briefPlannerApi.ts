import type { PlannerActionRequest, PlannerCreate, PlannerDetail, PlannerEvent, PlannerFrozenBrief, PlannerOfflineCreate, PlannerPlan, PlannerScenario, PlannerSource, PlannerSummary, PlannerWrite } from './briefPlannerTypes';

export class BriefPlannerError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
const id = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{32}$/.test(v);
const hash = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const count = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const positive = (v: unknown): v is number => count(v) && v >= 1;
const key = (v: unknown): v is string => typeof v === 'string' && /^[A-Za-z0-9_-]{8,128}$/.test(v);
const text = (v: unknown): v is string => typeof v === 'string';
export const PLANNER_SCENARIOS: PlannerScenario[] = ['success', 'repair', 'invalid_tool', 'step_limit', 'response_unknown'];
function invalid(): never { throw new BriefPlannerError(0, 'INVALID_RESPONSE', '规划任务响应与 D08 v1 不一致，请保留原请求并重新查询。'); }
function object(v: unknown): Record<string, unknown> { if (!v || typeof v !== 'object' || Array.isArray(v)) return invalid(); return v as Record<string, unknown>; }
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
  return { id: d.id, revision: d.revision, mode: d.mode as PlannerSummary['mode'], scenario: d.scenario as PlannerSummary['scenario'], status: d.status as PlannerSummary['status'],
    blocked_reason: d.blocked_reason as string | null, allowed_actions: d.allowed_actions as PlannerSummary['allowed_actions'], created_at: d.created_at, updated_at: d.updated_at,
    source: readPlannerSource(d.source), authorization_status: d.authorization_status as PlannerSummary['authorization_status'], execution_enabled: false, quality_status: 'not_reviewed',
    step_count: d.step_count, repair_count: d.repair_count, pending_call: pending, plan_version: d.plan_version };
}
function readPlan(value: unknown, snapshot: string): PlannerPlan {
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
  return { ...summary, client_request_key: d.client_request_key, limits: { max_steps: 6, max_repairs: 2, max_output_bytes: 65536, max_context_bytes: 1048576 },
    plan: d.plan === null ? null : readPlan(d.plan, summary.source.snapshot_hash), plan_versions: versions };
}
function write(data: Record<string, unknown>): PlannerWrite {
  if (typeof data.replayed !== 'boolean' || !positive(data.applied_revision)) return invalid();
  return { task: readPlannerDetail(data.task), replayed: data.replayed, applied_revision: data.applied_revision };
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
  async get(value: string) { return readPlannerDetail((await request(`${root}/tasks/${encodeURIComponent(value)}`)).task); },
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
