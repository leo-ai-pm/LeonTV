import { readPlannerPlan, readPlannerSource } from './briefPlannerApi';
import { EXECUTION_SCOPE, type ExecutionActionRequest, type ExecutionChatOutputParameter, type ExecutionDetail, type ExecutionEvent, type ExecutionGrant, type ExecutionGrantRequest, type ExecutionLedger, type ExecutionOperation, type ExecutionPolicy, type ExecutionPolicyResult, type ExecutionQuote, type ExecutionQuoteRequest, type ExecutionRevokeRequest, type ExecutionResponseStatus, type ExecutionWireApi, type ExecutionWrite } from './plannerExecutionTypes';

export class PlannerExecutionError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
const id = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{32}$/.test(v);
const hash = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const text = (v: unknown): v is string => typeof v === 'string';
const count = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const positive = (v: unknown): v is number => count(v) && v > 0;
const money = (v: unknown): v is string => text(v) && /^\d+\.\d{6}$/.test(v);
const nullableText = (v: unknown): v is string | null => v === null || text(v);
const nullableMoney = (v: unknown): v is string | null => v === null || money(v);
const nullableCount = (v: unknown): v is number | null => v === null || count(v);
function invalid(): never { throw new PlannerExecutionError(0, 'INVALID_RESPONSE', '规划记录响应格式不符合要求，原请求与未决费用保持，请重新查询。'); }
function object(v: unknown): Record<string, unknown> { if (!v || typeof v !== 'object' || Array.isArray(v)) return invalid(); return v as Record<string, unknown>; }
function wireApi(value: unknown): ExecutionWireApi {
  // D10's display-only legacy default must not backfill or rehash any saved object.
  if (value === undefined) return 'chat_completions';
  if (value !== 'chat_completions' && value !== 'responses') return invalid();
  return value;
}
function chatOutputProjection(data: Record<string, unknown>, wire: ExecutionWireApi): { chat_output_parameter?: ExecutionChatOutputParameter } {
  const present = Object.prototype.hasOwnProperty.call(data, 'chat_output_parameter');
  if (wire === 'responses') {
    if (present) return invalid();
    return {};
  }
  const parameter = present ? data.chat_output_parameter : 'max_tokens';
  if (parameter !== 'max_tokens' && parameter !== 'max_completion_tokens') return invalid();
  return { chat_output_parameter: parameter };
}
export function readExecutionPolicy(value: unknown): ExecutionPolicy {
  const p = object(value), wire = wireApi(p.wire_api);
  const output = chatOutputProjection(p, wire);
  if (!text(p.id) || !text(p.version) || !hash(p.policy_hash) || p.provider !== 'openai_compatible' || !text(p.model) || !p.model || !text(p.endpoint) || p.currency !== 'CNY' ||
    !text(p.input_cny_per_million) || !/^\d+(?:\.\d+)?$/.test(p.input_cny_per_million) || !text(p.output_cny_per_million) || !/^\d+(?:\.\d+)?$/.test(p.output_cny_per_million) ||
    !positive(p.max_input_tokens) || !positive(p.max_output_tokens) || !positive(p.max_input_bytes) || !positive(p.max_output_bytes) ||
    typeof p.request_deadline_seconds !== 'number' || !Number.isFinite(p.request_deadline_seconds) || p.request_deadline_seconds <= 0 || !positive(p.max_calls) ||
    !money(p.task_limit_cny) || !money(p.account_limit_cny) || !positive(p.account_max_calls) || !text(p.valid_until) || p.privacy_scope !== EXECUTION_SCOPE ||
    !text(p.pricing_basis) || !text(p.input_bound_basis)) return invalid();
  let endpoint: URL;
  try { endpoint = new URL(p.endpoint); } catch { return invalid(); }
  if (endpoint.protocol !== 'https:' || endpoint.username || endpoint.password || endpoint.search || endpoint.hash ||
    !endpoint.pathname.endsWith(wire === 'responses' ? '/responses' : '/chat/completions')) return invalid();
  return { wire_api: wire, ...output, id: p.id, version: p.version, policy_hash: p.policy_hash, provider: 'openai_compatible', model: p.model, endpoint: p.endpoint, currency: 'CNY',
    input_cny_per_million: p.input_cny_per_million, output_cny_per_million: p.output_cny_per_million, max_input_tokens: p.max_input_tokens,
    max_output_tokens: p.max_output_tokens, max_input_bytes: p.max_input_bytes, max_output_bytes: p.max_output_bytes, request_deadline_seconds: p.request_deadline_seconds,
    max_calls: p.max_calls, task_limit_cny: p.task_limit_cny, account_limit_cny: p.account_limit_cny, account_max_calls: p.account_max_calls,
    valid_until: p.valid_until, privacy_scope: EXECUTION_SCOPE, pricing_basis: p.pricing_basis, input_bound_basis: p.input_bound_basis };
}
export function readExecutionQuote(value: unknown): ExecutionQuote {
  const q = object(value);
  if (!id(q.id) || !id(q.task_id) || !positive(q.planner_task_revision) || !hash(q.snapshot_hash) || !money(q.per_call_hold_cny) || !money(q.total_limit_cny) ||
    !positive(q.max_calls) || !text(q.expires_at) || !hash(q.disclosure_hash) || !text(q.created_at) || !['valid','expired','policy_stale'].includes(String(q.status))) return invalid();
  return { id: q.id, task_id: q.task_id, planner_task_revision: q.planner_task_revision, snapshot_hash: q.snapshot_hash, policy: readExecutionPolicy(q.policy),
    per_call_hold_cny: q.per_call_hold_cny, total_limit_cny: q.total_limit_cny, max_calls: q.max_calls, expires_at: q.expires_at,
    disclosure_hash: q.disclosure_hash, created_at: q.created_at, status: q.status as ExecutionQuote['status'] };
}
export function readExecutionGrant(value: unknown): ExecutionGrant {
  const g = object(value);
  if (!id(g.id) || !id(g.task_id) || !id(g.quote_id) || !hash(g.snapshot_hash) || !hash(g.policy_hash) || !hash(g.disclosure_hash) || g.scope !== EXECUTION_SCOPE ||
    !['active','expired','revoked','policy_stale'].includes(String(g.status)) || !money(g.total_limit_cny) || !positive(g.max_calls) || !text(g.expires_at) || !text(g.created_at) || !nullableText(g.revoked_at)) return invalid();
  return { id: g.id, task_id: g.task_id, quote_id: g.quote_id, snapshot_hash: g.snapshot_hash, policy_hash: g.policy_hash, disclosure_hash: g.disclosure_hash, scope: EXECUTION_SCOPE,
    status: g.status as ExecutionGrant['status'], total_limit_cny: g.total_limit_cny, max_calls: g.max_calls, expires_at: g.expires_at, created_at: g.created_at, revoked_at: g.revoked_at };
}
function readLedger(value: unknown): ExecutionLedger {
  const l = object(value);
  if (l.currency !== 'CNY' || !money(l.locally_accounted_cny) || !money(l.held_cny) || !nullableMoney(l.task_limit_cny) || !count(l.call_count) || !nullableCount(l.max_calls) ||
    !money(l.account_locally_accounted_cny) || !money(l.account_held_cny) || !nullableMoney(l.account_limit_cny) || !count(l.account_call_count) || !nullableCount(l.account_max_calls) || l.provider_bill_confirmed !== false) return invalid();
  return { currency: 'CNY', locally_accounted_cny: l.locally_accounted_cny, held_cny: l.held_cny, task_limit_cny: l.task_limit_cny,
    call_count: l.call_count, max_calls: l.max_calls, account_locally_accounted_cny: l.account_locally_accounted_cny,
    account_held_cny: l.account_held_cny, account_limit_cny: l.account_limit_cny, account_call_count: l.account_call_count,
    account_max_calls: l.account_max_calls, provider_bill_confirmed: false };
}
function readOperation(value: unknown): ExecutionOperation {
  const o = object(value), wire = wireApi(o.wire_api), responseStatus = o.response_status === undefined ? null : o.response_status;
  const output = chatOutputProjection(o, wire);
  const cached = o.cached_input_tokens === undefined ? null : o.cached_input_tokens;
  const reasoning = o.reasoning_output_tokens === undefined ? null : o.reasoning_output_tokens;
  if (!id(o.id) || !positive(o.step) || !['submitting','received','settled','needs_reconciliation'].includes(String(o.status)) || !id(o.grant_id) || !id(o.quote_id) ||
    !hash(o.policy_hash) || !hash(o.context_hash) || !money(o.hold_cny) || !nullableMoney(o.locally_accounted_cny) || !nullableText(o.provider_request_id) || !nullableText(o.provider_response_id) ||
    !nullableCount(o.input_tokens) || !nullableCount(o.output_tokens) || !nullableText(o.finish_reason) || !['none','received','confirmed_local','unconfirmed'].includes(String(o.receipt_status)) || !text(o.created_at) || !nullableText(o.received_at) ||
    !(responseStatus === null || text(responseStatus) && ['completed','incomplete','failed','in_progress','queued','cancelled'].includes(responseStatus)) ||
    !nullableCount(cached) || !nullableCount(reasoning) ||
    wire === 'chat_completions' && responseStatus !== null || wire === 'responses' && o.finish_reason !== null ||
    cached !== null && (o.input_tokens === null || cached > o.input_tokens) || reasoning !== null && (o.output_tokens === null || reasoning > o.output_tokens)) return invalid();
  return { wire_api: wire, ...output, response_status: responseStatus as ExecutionResponseStatus | null, cached_input_tokens: cached, reasoning_output_tokens: reasoning,
    id: o.id, step: o.step, status: o.status as ExecutionOperation['status'], grant_id: o.grant_id, quote_id: o.quote_id, policy_hash: o.policy_hash, context_hash: o.context_hash,
    hold_cny: o.hold_cny, locally_accounted_cny: o.locally_accounted_cny, provider_request_id: o.provider_request_id, provider_response_id: o.provider_response_id,
    input_tokens: o.input_tokens, output_tokens: o.output_tokens, finish_reason: o.finish_reason, receipt_status: o.receipt_status as ExecutionOperation['receipt_status'], created_at: o.created_at, received_at: o.received_at };
}
export function readExecutionDetail(value: unknown): ExecutionDetail {
  const d = object(value);
  if (!id(d.task_id) || !count(d.revision) || !text(d.environment) || !['disabled','synthetic_test','live'].includes(d.environment) || !['not_authorized','ready','queued','running','stopped','blocked','needs_reconciliation','plan_ready'].includes(String(d.status)) ||
    !nullableText(d.blocked_reason) || !Array.isArray(d.allowed_actions) || !d.allowed_actions.every(a => a === 'resume' || a === 'stop' || a === 'reconcile') || typeof d.policy_available !== 'boolean' ||
    !(d.active_grant_id === null || id(d.active_grant_id)) || !Array.isArray(d.quotes) || !Array.isArray(d.grants) || !Array.isArray(d.operations) || d.quality_status !== 'not_reviewed' ||
    !nullableText(d.created_at) || !nullableText(d.updated_at)) return invalid();
  const source = readPlannerSource(d.source), quotes = d.quotes.map(readExecutionQuote), grants = d.grants.map(readExecutionGrant);
  if (quotes.some(q => q.task_id !== d.task_id || q.snapshot_hash !== source.snapshot_hash) || grants.some(g => g.task_id !== d.task_id || g.snapshot_hash !== source.snapshot_hash)) return invalid();
  return { task_id: d.task_id, revision: d.revision, environment: d.environment, status: d.status as ExecutionDetail['status'], blocked_reason: d.blocked_reason,
    allowed_actions: d.allowed_actions as ExecutionDetail['allowed_actions'], source, policy_available: d.policy_available, active_grant_id: d.active_grant_id,
    quotes, grants, ledger: readLedger(d.ledger), operations: d.operations.map(readOperation), plan: d.plan === null ? null : readPlannerPlan(d.plan, source.snapshot_hash),
    quality_status: 'not_reviewed', created_at: d.created_at, updated_at: d.updated_at };
}
async function request(url: string, method = 'GET', body?: unknown) {
  const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 15000);
  try {
    const response = await fetch(url, { method, cache: 'no-store', signal: abort.signal, ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
    const data: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : null;
      const code = detail && typeof detail === 'object' && 'code' in detail && text(detail.code) ? detail.code : `HTTP_${response.status}`;
      const messages: Record<string, string> = {
        INVALID_INPUT: '规划请求不符合当前要求，请核对任务与报价引用。', NOT_FOUND: '尚未查到原执行记录或原请求键，请保留原请求。',
        KEY_CONFLICT: '原键已绑定其他执行请求，请先查回，不可换键绕过。', REVISION_CONFLICT: '规划记录已更新，本次新动作未提交。请核对最新记录后重新选择。',
        TASK_NOT_ELIGIBLE: '离线任务不能使用正式执行控制，也不能升级为真实模型执行。', ACTION_NOT_ALLOWED: '当前不允许该执行动作，请刷新并核对未决记录。',
        LIVE_DISABLED: '真实模型传输默认关闭，当前不能报价、授权或执行。', POLICY_UNAVAILABLE: '尚无可用的已核实服务端策略，当前不能报价、授权或执行。',
        QUOTE_EXPIRED: '报价已过期，请核对原记录后重新获取报价。', POLICY_STALE: '策略已变化，旧报价或授权不能用于新提交。',
        CONSENT_MISMATCH: '同意范围或报价披露绑定不一致，请重新核对所显示的报价。',
        PRIVATE_STORAGE_UNAVAILABLE: '私有执行存储暂不可用，原请求与费用预占保留，请稍后查询。', CORRUPT_STORAGE: '执行记录无法校验，已停止读写；请保留原记录。',
      };
      throw new PlannerExecutionError(response.status, code, messages[code] || `执行请求未确认（HTTP ${response.status}），请查回原请求。`);
    }
    return object(data);
  } catch (error) {
    if (abort.signal.aborted || error instanceof TypeError) throw new PlannerExecutionError(0, 'OUTCOME_UNKNOWN', method === 'GET' ? '执行查询超时或网络中断，请重新查询。' : '执行响应未知，原键、原请求与未决预占保留；请先查询或按原键确认。');
    throw error;
  } finally { clearTimeout(timer); }
}
function write(d: Record<string, unknown>, resource?: 'quote' | 'grant'): ExecutionWrite {
  if (typeof d.replayed !== 'boolean' || !count(d.applied_revision)) return invalid();
  return { execution: readExecutionDetail(d.execution), replayed: d.replayed, applied_revision: d.applied_revision,
    ...(resource === 'quote' ? { quote: readExecutionQuote(d.quote) } : resource === 'grant' ? { grant: readExecutionGrant(d.grant) } : {}) };
}
const root = (id: string) => `/api/planner/tasks/${encodeURIComponent(id)}/execution`;
export const plannerExecutionApi = {
  async policy(): Promise<ExecutionPolicyResult> {
    const d = await request('/api/planner/execution/policy');
    if (typeof d.available !== 'boolean' || !nullableText(d.blocked_reason) || !text(d.environment) || !['disabled','synthetic_test','live'].includes(d.environment) || (d.available ? d.policy === null : d.policy !== null)) return invalid();
    return { available: d.available, blocked_reason: d.blocked_reason, environment: d.environment, policy: d.policy === null ? null : readExecutionPolicy(d.policy) };
  },
  async get(id: string) { return readExecutionDetail((await request(root(id))).execution); },
  async quote(id: string, input: ExecutionQuoteRequest) { return write(await request(`${root(id)}/quotes`, 'POST', input), 'quote'); },
  async quoteByKey(id: string, key: string) { return readExecutionQuote((await request(`${root(id)}/quotes/by-key/${encodeURIComponent(key)}`)).quote); },
  async grant(id: string, input: ExecutionGrantRequest) { return write(await request(`${root(id)}/grants`, 'POST', input), 'grant'); },
  async grantByKey(id: string, key: string) { return readExecutionGrant((await request(`${root(id)}/grants/by-key/${encodeURIComponent(key)}`)).grant); },
  async revoke(id: string, grant: string, input: ExecutionRevokeRequest) { return write(await request(`${root(id)}/grants/${encodeURIComponent(grant)}/revoke`, 'POST', input), 'grant'); },
  async action(id: string, input: ExecutionActionRequest) { return write(await request(`${root(id)}/actions`, 'POST', input)); },
  async events(taskId: string, after = 0) {
    const d = await request(`${root(taskId)}/events?after_sequence=${after}&limit=100`);
    if (!Array.isArray(d.events) || !count(d.next_sequence) || typeof d.has_more !== 'boolean') return invalid();
    const events: ExecutionEvent[] = d.events.map(value => {
      const e = object(value);
      if (!positive(e.sequence) || !count(e.execution_revision) || !text(e.type) || !text(e.created_at) || !(e.operation_id === null || id(e.operation_id)) || !nullableText(e.code)) return invalid();
      return { sequence: e.sequence, type: e.type, execution_revision: e.execution_revision, created_at: e.created_at, operation_id: e.operation_id, code: e.code };
    });
    if (events.some((e, i) => e.sequence !== after + i + 1) || d.next_sequence !== (events.at(-1)?.sequence ?? after)) return invalid();
    return { events, next_sequence: d.next_sequence, has_more: d.has_more };
  },
};
