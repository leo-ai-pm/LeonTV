import { plannerExecutionApi, PlannerExecutionError } from './plannerExecutionApi';
import { EMPIRICAL_EXECUTION_SCOPE, EXECUTION_SCOPE, type ExecutionAction, type ExecutionDetail, type ExecutionEvent, type ExecutionGrant, type ExecutionIntent, type ExecutionPolicyResult, type ExecutionQuote, type ExecutionWrite } from './plannerExecutionTypes';
import { empiricalExecutionBlocked, executionPolicyMatchesProfile, isEmpiricalExecution } from './plannerExecutionRisk';
import type { PlannerSummary } from './briefPlannerTypes';

interface StoragePort { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void }
type Binding = Pick<PlannerSummary, 'id' | 'revision' | 'mode' | 'source'>;
interface ExecutionState {
  binding: Binding | null; execution: ExecutionDetail | null; policy: ExecutionPolicyResult | null; quoteId: string | null;
  reviewed: string | null; pending: ExecutionIntent | null; busy: boolean; loading: boolean; refreshing: boolean;
  error: string; notice: string; storageBlocked: boolean; events: ExecutionEvent[]; nextSequence: number; moreEvents: boolean;
}
const initial = (): ExecutionState => ({ binding: null, execution: null, policy: null, quoteId: null, reviewed: null, pending: null,
  busy: false, loading: false, refreshing: false, error: '', notice: '', storageBlocked: false, events: [], nextSequence: 0, moreEvents: false });
const id = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{32}$/.test(v);
const hash = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const key = (v: unknown) => typeof v === 'string' && /^[A-Za-z0-9_-]{8,128}$/.test(v);
const revision = (v: unknown) => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const message = (error: unknown) => error instanceof PlannerExecutionError ? error.message : '执行记录未确认，原请求和未决费用保持，请重新查询。';
function readIntent(raw: string | null): ExecutionIntent | null {
  if (raw === null) return null;
  const d = JSON.parse(raw), r = d?.request;
  if (!d || d.version !== 1 || !id(d.task_id) || !hash(d.snapshot_hash) || !r || typeof r !== 'object' || !revision(r.expected_execution_revision)) throw new Error('invalid');
  const base = { version: 1 as const, task_id: d.task_id as string, snapshot_hash: d.snapshot_hash as string };
  if (d.kind === 'quote' && key(r.client_request_key) && revision(r.expected_task_revision) && r.expected_task_revision > 0)
    return { ...base, kind: 'quote', request: { client_request_key: r.client_request_key, expected_execution_revision: r.expected_execution_revision, expected_task_revision: r.expected_task_revision } };
  if (d.kind === 'grant' && key(r.client_request_key) && id(r.quote_id) && hash(r.accepted_disclosure_hash) && (r.accepted_scope === EXECUTION_SCOPE || r.accepted_scope === EMPIRICAL_EXECUTION_SCOPE) && r.consent === true)
    return { ...base, kind: 'grant', request: { client_request_key: r.client_request_key, expected_execution_revision: r.expected_execution_revision, quote_id: r.quote_id,
      accepted_disclosure_hash: r.accepted_disclosure_hash, accepted_scope: r.accepted_scope, consent: true } };
  if (d.kind === 'revoke' && id(d.grant_id) && key(r.client_action_key))
    return { ...base, kind: 'revoke', grant_id: d.grant_id, request: { client_action_key: r.client_action_key, expected_execution_revision: r.expected_execution_revision } };
  if (d.kind === 'action' && key(r.client_action_key) && ['resume','stop','reconcile'].includes(r.action))
    return { ...base, kind: 'action', request: { client_action_key: r.client_action_key, expected_execution_revision: r.expected_execution_revision, action: r.action } };
  throw new Error('invalid');
}
export function executionHasUnknownCall(execution: ExecutionDetail) {
  return empiricalExecutionBlocked(execution) || execution.status === 'needs_reconciliation' || execution.operations.some(o => o.status === 'submitting' || o.status === 'needs_reconciliation' || o.receipt_status === 'unconfirmed');
}
export class PlannerExecutionController {
  readonly storageKey = 'leontv.execution.pending.v1';
  private state = initial();
  private epoch = 0;
  private observation = 0;
  private locked = false;
  private listeners = new Set<() => void>();
  constructor(private storage: () => StoragePort) {}
  getSnapshot = () => this.state;
  getServerSnapshot = () => this.state;
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  private set(patch: Partial<ExecutionState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(fn => fn()); }
  invalidate() { this.epoch++; this.observation++; }
  report(error: unknown) { this.set({ error: message(error) }); }
  async initialize(task: Binding) {
    try { this.set({ pending: readIntent(this.storage().getItem(this.storageKey)), storageBlocked: false }); }
    catch { this.set({ storageBlocked: true, error: '执行恢复记录不可读，已阻止新提交，请保留原记录。' }); }
    await this.select(task, true);
  }
  async select(task: Binding, force = false) {
    const changed = this.state.binding?.id !== task.id || this.state.binding?.mode !== task.mode || this.state.binding?.source.snapshot_hash !== task.source.snapshot_hash;
    if (!changed && !force) {
      if (this.state.binding?.revision !== task.revision) this.set({ binding: task, reviewed: null });
      return;
    }
    // Parent object identity and repeated GETs must not invalidate an in-flight write ACK.
    // Only a changed task/source/mode or unmount advances the binding generation.
    if (changed) this.epoch++;
    const ticket = this.epoch, observation = ++this.observation;
    this.set({ binding: task, ...(changed ? { execution: null, policy: null, quoteId: null, reviewed: null, events: [], nextSequence: 0, moreEvents: false } : this.state.binding?.revision !== task.revision ? { reviewed: null } : {}),
      loading: task.mode === 'normal', refreshing: false, error: this.state.storageBlocked ? this.state.error : '', notice: '' });
    if (task.mode !== 'normal') return;
    try { await this.readView(task, ticket, observation); }
    finally { if (ticket === this.epoch && observation === this.observation) this.set({ loading: false }); }
  }
  private matches(execution: ExecutionDetail, taskId: string, snapshot: string) { return execution.task_id === taskId && execution.source.snapshot_hash === snapshot; }
  private apply(execution: ExecutionDetail, ticket: number) {
    const binding = this.state.binding;
    if (ticket !== this.epoch || !binding) return;
    if (!this.matches(execution, binding.id, binding.source.snapshot_hash)) throw new Error('binding');
    if (this.state.execution && this.state.execution.revision > execution.revision) return;
    const previous = this.state.execution;
    const chosen = execution.quotes.find(q => q.id === this.state.quoteId) || execution.quotes.at(-1);
    this.set({ execution, quoteId: chosen?.id ?? null, ...(previous?.revision !== execution.revision ? { reviewed: null } : {}) });
  }
  private async readView(binding: Binding, ticket: number, observation: number) {
    const [execution, policy] = await Promise.allSettled([plannerExecutionApi.get(binding.id), plannerExecutionApi.policy()]);
    if (ticket !== this.epoch || observation !== this.observation) return;
    try {
      if (policy.status === 'fulfilled') {
        if (this.state.policy?.policy?.policy_hash !== policy.value.policy?.policy_hash || this.state.policy?.available !== policy.value.available) this.set({ reviewed: null });
        this.set({ policy: policy.value });
      } else this.set({ policy: null, reviewed: null });
      if (execution.status === 'fulfilled') { this.apply(execution.value, ticket); await this.loadEvents(); }
      if (execution.status === 'rejected') this.report(execution.reason);
      else if (policy.status === 'rejected') this.report(policy.reason);
    } catch (error) { if (ticket === this.epoch) this.report(error); }
  }
  async refresh() {
    const binding = this.state.binding, ticket = this.epoch;
    if (!binding || binding.mode !== 'normal' || this.state.loading || this.state.refreshing) return;
    const observation = ++this.observation;
    this.set({ refreshing: true, error: '' });
    try { await this.readView(binding, ticket, observation); }
    finally { if (ticket === this.epoch && observation === this.observation) this.set({ refreshing: false }); }
  }
  async loadEvents() {
    const binding = this.state.binding, ticket = this.epoch, after = this.state.nextSequence;
    if (!binding || binding.mode !== 'normal') return;
    try {
      const page = await plannerExecutionApi.events(binding.id, after);
      if (ticket === this.epoch && this.state.nextSequence === after) this.set({ events: [...this.state.events, ...page.events], nextSequence: page.next_sequence, moreEvents: page.has_more });
    } catch (error) { if (ticket === this.epoch) this.report(error); }
  }
  chooseQuote(quoteId: string) { if (this.state.execution?.quotes.some(q => q.id === quoteId)) this.set({ quoteId, reviewed: null }); }
  private quote() { return this.state.execution?.quotes.find(q => q.id === this.state.quoteId); }
  private reviewToken() {
    const q = this.quote(), b = this.state.binding, e = this.state.execution;
    return q && b && e ? `${b.id}:${b.revision}:${e.revision}:${q.id}:${q.disclosure_hash}:${q.snapshot_hash}` : null;
  }
  quoteGate(): string | null {
    const q = this.quote(), b = this.state.binding, e = this.state.execution, p = this.state.policy;
    if (!b || b.mode !== 'normal') return '离线任务不能使用正式执行授权。';
    if (!e || this.state.loading || this.state.refreshing) return '请等待读取规划记录。';
    if (!p?.available || !p.policy || !e.policy_available) return '服务端传输关闭或核实策略不可用，不能授权。';
    if (!executionPolicyMatchesProfile(e, p.policy)) return '当前策略与此任务冻结的执行模式不同，旧任务和授权不能升级；原待确认请求保持。';
    if (!q) return '先获取并核对服务端报价。';
    if (q.task_id !== b.id || q.snapshot_hash !== b.source.snapshot_hash || q.planner_task_revision !== b.revision) return '报价与当前任务版本不一致，请重新报价。';
    if (q.status !== 'valid' || !Number.isFinite(Date.parse(q.expires_at)) || Date.parse(q.expires_at) <= Date.now()) return '报价已过期或策略已变化，请重新报价。';
    if (!Number.isFinite(Date.parse(q.policy.valid_until)) || Date.parse(q.policy.valid_until) <= Date.now()) return '报价绑定的策略已过期，请重新报价。';
    if (q.policy.policy_hash !== p.policy.policy_hash) return '报价策略已变化，请重新报价。';
    return null;
  }
  reviewQuote(accepted: boolean) { this.set({ reviewed: accepted && !this.quoteGate() ? this.reviewToken() : null }); }
  private available() {
    return !this.locked && !this.state.pending && !this.state.storageBlocked && !this.state.loading && !this.state.refreshing && !this.state.error &&
      this.state.binding?.mode === 'normal' && Boolean(this.state.execution);
  }
  private intentBase() { return { version: 1 as const, task_id: this.state.binding!.id, snapshot_hash: this.state.binding!.source.snapshot_hash }; }
  async requestQuote() {
    if (!this.available()) return;
    if (!this.state.policy?.available || !this.state.execution!.policy_available) { this.set({ error: '真实模型调用未开放或模型与费用策略尚未就绪，当前不能获取报价。' }); return; }
    if (!this.state.policy?.policy || !executionPolicyMatchesProfile(this.state.execution!, this.state.policy.policy)) { this.set({ error: '当前策略与此任务冻结的执行模式不同，不能升级旧任务或授权；原请求保持。' }); return; }
    await this.submit({ ...this.intentBase(), kind: 'quote', request: { client_request_key: crypto.randomUUID(), expected_execution_revision: this.state.execution!.revision, expected_task_revision: this.state.binding!.revision } });
  }
  async grant() {
    if (!this.available()) return;
    const gate = this.quoteGate();
    if (gate || !this.state.reviewed || this.state.reviewed !== this.reviewToken()) { this.set({ error: gate || '请先明确核对并同意当前报价与外发范围。' }); return; }
    const q = this.quote()!;
    await this.submit({ ...this.intentBase(), kind: 'grant', request: { client_request_key: crypto.randomUUID(), expected_execution_revision: this.state.execution!.revision,
      quote_id: q.id, accepted_disclosure_hash: q.disclosure_hash, accepted_scope: q.policy.privacy_scope, consent: true } });
  }
  async revoke(grantId: string) {
    if (!this.available() || !this.state.execution!.grants.some(g => g.id === grantId && g.status === 'active')) return;
    await this.submit({ ...this.intentBase(), kind: 'revoke', grant_id: grantId, request: { client_action_key: crypto.randomUUID(), expected_execution_revision: this.state.execution!.revision } });
  }
  async action(action: ExecutionAction) {
    if (!this.available()) return;
    const e = this.state.execution!;
    if (!e.allowed_actions.includes(action)) { this.set({ error: '服务器当前不允许此执行动作。' }); return; }
    if (action === 'resume' && (!this.state.policy?.available || !e.policy_available || executionHasUnknownCall(e))) { this.set({ error: '模型配置不可用或仍有未确认调用，暂不能开始或继续规划。未决预占保持。' }); return; }
    if (action === 'reconcile' && isEmpiricalExecution(e)) { this.set({ error: '平台费用仅由服务端核对，当前界面不能代替核费或释放风险预留。' }); return; }
    if (action === 'reconcile' && !e.operations.some(o => o.received_at !== null && o.receipt_status !== 'none')) { this.set({ error: '没有可处理的持久回执，不能对账或释放未决预占。' }); return; }
    await this.submit({ ...this.intentBase(), kind: 'action', request: { client_action_key: crypto.randomUUID(), expected_execution_revision: e.revision, action } });
  }
  private persist(intent: ExecutionIntent) {
    try {
      if (readIntent(this.storage().getItem(this.storageKey))) throw new Error('pending');
      this.storage().setItem(this.storageKey, JSON.stringify(intent)); this.set({ pending: intent });
    } catch { this.set({ storageBlocked: true }); throw new PlannerExecutionError(0, 'STORAGE_BLOCKED', '无法安全保存原执行请求，已阻止发送。请先查询原请求或检查浏览器存储。'); }
  }
  private clear(intent: ExecutionIntent) {
    const current = this.storage().getItem(this.storageKey);
    if (current !== null && JSON.stringify(readIntent(current)) !== JSON.stringify(intent)) throw new Error('other intent');
    this.storage().removeItem(this.storageKey); this.set({ pending: null, storageBlocked: false });
  }
  private async submit(intent: ExecutionIntent) {
    this.locked = true; this.set({ busy: true, error: '', notice: '', reviewed: null }); const ticket = this.epoch;
    try { this.persist(intent); await this.dispatch(intent, ticket); }
    catch (error) { if (ticket === this.epoch) this.report(error); }
    finally { this.locked = false; this.set({ busy: false }); }
  }
  private checkResource(resource: ExecutionQuote | ExecutionGrant, intent: ExecutionIntent) {
    if (resource.task_id !== intent.task_id || resource.snapshot_hash !== intent.snapshot_hash) throw new Error('binding');
    if (intent.kind === 'quote' && (!('planner_task_revision' in resource) || resource.planner_task_revision !== intent.request.expected_task_revision)) throw new Error('quote binding');
    if (intent.kind === 'grant' && (!('quote_id' in resource) || resource.quote_id !== intent.request.quote_id || resource.disclosure_hash !== intent.request.accepted_disclosure_hash || resource.scope !== intent.request.accepted_scope)) throw new Error('grant binding');
    if (intent.kind === 'revoke' && resource.id !== intent.grant_id) throw new Error('revoke binding');
  }
  private async dispatch(intent: ExecutionIntent, ticket: number) {
    try {
      let result: ExecutionWrite;
      if (intent.kind === 'quote') result = await plannerExecutionApi.quote(intent.task_id, intent.request);
      else if (intent.kind === 'grant') result = await plannerExecutionApi.grant(intent.task_id, intent.request);
      else if (intent.kind === 'revoke') result = await plannerExecutionApi.revoke(intent.task_id, intent.grant_id, intent.request);
      else result = await plannerExecutionApi.action(intent.task_id, intent.request);
      if (!this.matches(result.execution, intent.task_id, intent.snapshot_hash)) throw new Error('binding');
      if (intent.kind === 'quote') this.checkResource(result.quote!, intent);
      if (intent.kind === 'grant' || intent.kind === 'revoke') this.checkResource(result.grant!, intent);
      this.clear(intent);
      if (ticket === this.epoch) {
        const selected = this.state.binding?.id === intent.task_id && this.state.binding?.source.snapshot_hash === intent.snapshot_hash;
        if (selected) {
          this.apply(result.execution, ticket);
          if (result.quote) this.set({ quoteId: result.quote.id, reviewed: null });
        }
        this.set({ notice: !selected ? '原任务请求已按原键确认，当前任务选择保持。' : result.replayed ? '已按原键确认原操作，以下为最新规划记录。' : intent.kind === 'grant' ? '授权已保存，不会因此自动开始规划。' : intent.kind === 'revoke' ? '撤销已记录，只阻止后续新提交；在途调用与未决预占保持。' : intent.kind === 'quote' ? '报价已保存，请核对完整披露；尚未授权或执行。' : '规划操作已确认，以下为最新记录。' });
        if (selected) await this.loadEvents();
      }
    } catch (error) {
      if (error instanceof PlannerExecutionError && ['INVALID_INPUT','REVISION_CONFLICT','ACTION_NOT_ALLOWED','TASK_NOT_ELIGIBLE','LIVE_DISABLED','POLICY_UNAVAILABLE','QUOTE_EXPIRED','POLICY_STALE','CONSENT_MISMATCH'].includes(error.code)) {
        this.clear(intent); if (ticket === this.epoch) await this.refresh();
      }
      throw error;
    }
  }
  async recover(retry: boolean) {
    if (this.locked) return;
    let intent: ExecutionIntent | null;
    try { intent = readIntent(this.storage().getItem(this.storageKey)); this.set({ pending: intent, storageBlocked: false }); }
    catch { this.set({ storageBlocked: true, error: '原执行请求记录不可读，已阻止新提交，请保留原记录。' }); return; }
    if (!intent) { this.set({ notice: '没有待确认的执行请求。' }); return; }
    this.locked = true; this.set({ busy: true, error: '', notice: '', reviewed: null }); const ticket = this.epoch;
    try {
      if (intent.kind === 'quote' || intent.kind === 'grant') {
        let resource: ExecutionQuote | ExecutionGrant;
        try { resource = intent.kind === 'quote' ? await plannerExecutionApi.quoteByKey(intent.task_id, intent.request.client_request_key) : await plannerExecutionApi.grantByKey(intent.task_id, intent.request.client_request_key); }
        catch (error) {
          if (retry && error instanceof PlannerExecutionError && error.code === 'NOT_FOUND') { await this.dispatch(intent, ticket); return; }
          throw error;
        }
        this.checkResource(resource, intent);
        const current = await plannerExecutionApi.get(intent.task_id);
        if (!this.matches(current, intent.task_id, intent.snapshot_hash)) throw new Error('binding');
        this.clear(intent);
        if (ticket === this.epoch && this.state.binding?.id === intent.task_id && this.state.binding?.source.snapshot_hash === intent.snapshot_hash) { this.apply(current, ticket); await this.loadEvents(); }
        if (ticket === this.epoch) this.set({ notice: '已按原键查回报价 / 授权，未自动开始执行；当前任务选择保持。' });
      } else {
        const current = await plannerExecutionApi.get(intent.task_id);
        if (!this.matches(current, intent.task_id, intent.snapshot_hash)) throw new Error('binding');
        if (ticket === this.epoch && this.state.binding?.id === intent.task_id && this.state.binding?.source.snapshot_hash === intent.snapshot_hash) this.apply(current, ticket);
        if (retry) await this.dispatch(intent, ticket);
        else if (ticket === this.epoch) this.set({ notice: '已读取规划记录；查询不能确认原动作键。原版本、动作与未决预占保持，请按原键确认。' });
      }
    } catch (error) { if (ticket === this.epoch) this.report(error); }
    finally { this.locked = false; this.set({ busy: false }); }
  }
}
