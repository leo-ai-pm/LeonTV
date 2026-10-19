import { BriefPlannerError, briefPlannerApi, PLANNER_SCENARIOS } from './briefPlannerApi';
import type { PlannerAction, PlannerBriefGate, PlannerDetail, PlannerEvent, PlannerFrozenBrief, PlannerIntent, PlannerMode, PlannerOfflineCreate, PlannerScenario, PlannerSummary } from './briefPlannerTypes';

interface StoragePort { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void }
interface PlannerState {
  tasks: PlannerSummary[]; nextOffset: number | null; task: PlannerDetail | null; source: PlannerFrozenBrief | null;
  events: PlannerEvent[]; nextSequence: number; moreEvents: boolean; pending: PlannerIntent | null;
  busy: boolean; loading: boolean; listLoading: boolean; refreshing: boolean; sourceLoading: boolean;
  error: string; notice: string; storageBlocked: boolean;
}
const initial = (): PlannerState => ({ tasks: [], nextOffset: null, task: null, source: null, events: [], nextSequence: 0, moreEvents: false,
  pending: null, busy: false, loading: false, listLoading: false, refreshing: false, sourceLoading: false, error: '', notice: '', storageBlocked: false });
const validId = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{32}$/.test(v);
const validKey = (v: unknown) => typeof v === 'string' && /^[A-Za-z0-9_-]{8,128}$/.test(v);
const positive = (v: unknown) => typeof v === 'number' && Number.isSafeInteger(v) && v >= 1;
const message = (error: unknown) => error instanceof BriefPlannerError ? error.message : '规划操作未确认，请保留原请求并重新查询。';
export function plannerGate(state: PlannerBriefGate): string | null {
  if (state.loading) return '正在读取简报，请等待保存版本确认。';
  if (state.saving || state.pending) return '简报保存结果尚未确认，请先查回原保存。';
  if (state.conflictDetected) return '简报存在版本冲突，请先核对并保存。';
  if (!state.document) return '请先保存创作简报，再准备规划任务。';
  if (!state.document.is_current) return '当前是历史版本，请回到当前简报核对并保存。';
  if (state.dirty) return '有未保存编辑，请先保存，规划只使用已保存版本。';
  return null;
}
function readIntent(raw: string | null): PlannerIntent | null {
  if (raw === null) return null;
  const d = JSON.parse(raw);
  if (!d || d.version !== 1 || !d.request || typeof d.request !== 'object') throw new Error('invalid');
  const r = d.request;
  if (d.kind === 'create' && validKey(r.client_request_key) && validId(r.brief_id) && positive(r.brief_revision) &&
      (d.mode === 'normal' || d.mode === 'offline_test' && PLANNER_SCENARIOS.includes(r.scenario))) {
    // Project only the exact original reference request, never text or browser authority.
    return { version: 1, kind: 'create', mode: d.mode, request: { client_request_key: r.client_request_key, brief_id: r.brief_id, brief_revision: r.brief_revision,
      ...(d.mode === 'offline_test' ? { scenario: r.scenario } : {}) } };
  }
  if (d.kind === 'action' && validId(d.task_id) && validKey(r.client_action_key) && positive(r.expected_revision) && ['stop','resume'].includes(r.action))
    return { version: 1, kind: 'action', task_id: d.task_id, request: { client_action_key: r.client_action_key, expected_revision: r.expected_revision, action: r.action } };
  throw new Error('invalid');
}
export class BriefPlannerController {
  readonly storageKey = 'leontv.planner.pending.v1';
  readonly selectionKey = 'leontv.planner.selected.v1';
  private state = initial();
  private listeners = new Set<() => void>();
  private epoch = 0;
  private listEpoch = 0;
  private locked = false;
  constructor(private storage: () => StoragePort) {}
  getSnapshot = () => this.state;
  getServerSnapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private set(patch: Partial<PlannerState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(l => l()); }
  report(error: unknown) { this.set({ error: message(error) }); }
  invalidate() { this.epoch++; this.listEpoch++; }
  async initialize() {
    const ticket = ++this.epoch;
    try {
      const pending = readIntent(this.storage().getItem(this.storageKey));
      if (ticket !== this.epoch) return;
      this.set({ pending, storageBlocked: false, error: '' });
      const selected = this.storage().getItem(this.selectionKey);
      if (pending) await this.recover(false);
      else if (validId(selected)) await this.open(selected);
    } catch { if (ticket === this.epoch) this.set({ storageBlocked: true, error: '无法读取规划恢复记录，已阻止新提交。请保留原记录并检查浏览器存储。' }); }
    void this.loadList();
  }
  async loadList(append = false) {
    const ticket = ++this.listEpoch, offset = append ? this.state.nextOffset : 0;
    if (offset === null) return;
    this.set({ listLoading: true });
    try {
      const result = await briefPlannerApi.list(offset);
      if (ticket !== this.listEpoch) return;
      const merged = new Map((append ? this.state.tasks : []).map(t => [t.id, t]));
      for (const task of result.tasks) { const previous = this.state.tasks.find(t => t.id === task.id); merged.set(task.id, previous && previous.revision > task.revision ? previous : task); }
      this.set({ tasks: [...merged.values()], nextOffset: result.next_offset });
    } catch (error) { if (ticket === this.listEpoch) this.report(error); }
    finally { if (ticket === this.listEpoch) this.set({ listLoading: false }); }
  }
  private remember(task: PlannerDetail) {
    const previous = this.state.tasks.find(t => t.id === task.id);
    if (previous && previous.revision > task.revision) return;
    // Adapter projection prevents accidental retention of plan/body in summary list.
    const { client_request_key, limits, plan, plan_versions, ...summary } = task;
    void [client_request_key, limits, plan, plan_versions];
    this.listEpoch++;
    this.set({ listLoading: false, tasks: [summary, ...this.state.tasks.filter(t => t.id !== task.id)] });
  }
  private accept(task: PlannerDetail, ticket: number) {
    this.remember(task);
    if (ticket !== this.epoch || this.state.task?.id === task.id && this.state.task.revision > task.revision) return;
    const changed = this.state.task?.id !== task.id;
    this.set({ task, ...(changed ? { source: null, events: [], nextSequence: 0, moreEvents: false } : {}) });
    try { this.storage().setItem(this.selectionKey, task.id); } catch { /* Selection is optional; write intents are not. */ }
  }
  async open(taskId: string) {
    if (!validId(taskId)) return;
    const ticket = ++this.epoch;
    this.set({ task: null, source: null, events: [], nextSequence: 0, moreEvents: false, loading: true, refreshing: false, sourceLoading: false, error: '', notice: '' });
    try {
      const task = await briefPlannerApi.get(taskId);
      if (task.id !== taskId) throw new Error('binding');
      this.accept(task, ticket);
      if (ticket === this.epoch) await this.loadEvents();
    } catch (error) { if (ticket === this.epoch) this.report(error); }
    finally { if (ticket === this.epoch) this.set({ loading: false }); }
  }
  async refresh() {
    const task = this.state.task, ticket = this.epoch;
    if (!task || this.state.refreshing) return;
    this.set({ refreshing: true, error: '' });
    try {
      const current = await briefPlannerApi.get(task.id);
      if (current.id !== task.id) throw new Error('binding');
      this.accept(current, ticket);
      if (ticket === this.epoch) await this.loadEvents();
    } catch (error) { if (ticket === this.epoch) this.report(error); }
    finally { if (ticket === this.epoch) this.set({ refreshing: false }); }
  }
  async loadEvents() {
    const task = this.state.task, ticket = this.epoch, after = this.state.nextSequence;
    if (!task) return;
    try {
      const page = await briefPlannerApi.events(task.id, after);
      if (ticket !== this.epoch || this.state.task?.id !== task.id || this.state.nextSequence !== after) return;
      this.set({ events: [...this.state.events, ...page.events], nextSequence: page.next_sequence, moreEvents: page.has_more });
    } catch (error) { if (ticket === this.epoch) this.report(error); }
  }
  async loadSource() {
    const task = this.state.task, ticket = this.epoch;
    if (!task || this.state.sourceLoading) return;
    this.set({ sourceLoading: true, error: '' });
    try {
      const source = await briefPlannerApi.source(task.id);
      if (source.snapshot_hash !== task.source.snapshot_hash || source.brief_id !== task.source.brief_id || source.brief_revision !== task.source.brief_revision) throw new Error('binding');
      if (ticket === this.epoch && this.state.task?.id === task.id) this.set({ source });
    } catch (error) { if (ticket === this.epoch) this.report(error); }
    finally { if (ticket === this.epoch) this.set({ sourceLoading: false }); }
  }
  private persist(intent: PlannerIntent) {
    try {
      if (readIntent(this.storage().getItem(this.storageKey))) throw new Error('pending');
      this.storage().setItem(this.storageKey, JSON.stringify(intent));
      this.set({ pending: intent });
    } catch { this.set({ storageBlocked: true }); throw new BriefPlannerError(0, 'STORAGE_BLOCKED', '未能安全保存原规划请求，已阻止发送。请先查回原请求或检查浏览器存储。'); }
  }
  private clear(intent: PlannerIntent) {
    const current = this.storage().getItem(this.storageKey);
    if (current !== null && JSON.stringify(readIntent(current)) !== JSON.stringify(intent)) throw new Error('other intent');
    this.storage().removeItem(this.storageKey);
    this.set({ pending: null, storageBlocked: false });
  }
  private matches(task: PlannerSummary, intent: Extract<PlannerIntent, { kind: 'create' }>) {
    return task.mode === intent.mode && task.source.brief_id === intent.request.brief_id && task.source.brief_revision === intent.request.brief_revision &&
      (intent.mode === 'normal' || task.scenario === (intent.request as PlannerOfflineCreate).scenario);
  }
  async create(getBrief: () => PlannerBriefGate, mode: PlannerMode = 'normal', scenario: PlannerScenario = 'success') {
    if (this.locked) return;
    const gate = plannerGate(getBrief());
    if (gate) { this.set({ error: gate }); return; }
    if (this.state.pending || this.state.storageBlocked) { this.set({ error: '请先确认原规划请求，不能换键新建。' }); return; }
    const doc = getBrief().document!;
    const intent: PlannerIntent = { version: 1, kind: 'create', mode, request: { client_request_key: crypto.randomUUID(), brief_id: doc.id, brief_revision: doc.revision,
      ...(mode === 'offline_test' ? { scenario } : {}) } };
    this.locked = true; this.set({ busy: true, error: '', notice: '' });
    const ticket = this.epoch;
    try { this.persist(intent); await this.dispatch(intent, ticket); }
    catch (error) { if (ticket === this.epoch) this.report(error); }
    finally { this.locked = false; this.set({ busy: false }); }
  }
  async action(action: PlannerAction) {
    if (this.locked) return;
    const task = this.state.task;
    if (!task || this.state.pending || this.state.storageBlocked || !task.allowed_actions.includes(action)) { this.set({ error: '当前任务不允许该动作，或仍有原请求待确认。' }); return; }
    const intent: PlannerIntent = { version: 1, kind: 'action', task_id: task.id, request: { client_action_key: crypto.randomUUID(), expected_revision: task.revision, action } };
    this.locked = true; this.set({ busy: true, error: '', notice: '' }); const ticket = this.epoch;
    try { this.persist(intent); await this.dispatch(intent, ticket); }
    catch (error) { if (ticket === this.epoch) this.report(error); }
    finally { this.locked = false; this.set({ busy: false }); }
  }
  private async dispatch(intent: PlannerIntent, ticket: number) {
    try {
      const result = intent.kind === 'create' ? await (intent.mode === 'normal' ? briefPlannerApi.create(intent.request) : briefPlannerApi.createOffline(intent.request as PlannerOfflineCreate)) : await briefPlannerApi.action(intent.task_id, intent.request);
      if (intent.kind === 'create' ? !this.matches(result.task, intent) || result.task.client_request_key !== intent.request.client_request_key : result.task.id !== intent.task_id) throw new Error('binding');
      this.clear(intent);
      this.accept(result.task, ticket);
      if (ticket === this.epoch) { this.set({ error: '', notice: result.replayed ? '已按原键确认任务当前记录。' : intent.kind === 'create' ? '规划任务已保存，来源版本已冻结。' : '动作已确认，以下为服务器当前状态。' }); await this.loadEvents(); }
    } catch (error) {
      if (error instanceof BriefPlannerError && (error.code === 'INVALID_INPUT' || intent.kind === 'action' && ['REVISION_CONFLICT','ACTION_NOT_ALLOWED'].includes(error.code))) {
        this.clear(intent);
        if (ticket === this.epoch && intent.kind === 'action') await this.refresh();
      }
      throw error;
    }
  }
  async recover(retry: boolean) {
    if (this.locked) return;
    let intent: PlannerIntent | null;
    try { intent = readIntent(this.storage().getItem(this.storageKey)); this.set({ pending: intent, storageBlocked: false }); }
    catch { this.set({ storageBlocked: true, error: '规划恢复记录不可读，已阻止新提交；请保留原记录。' }); return; }
    if (!intent) { this.set({ notice: '没有待确认的规划请求。' }); return; }
    this.locked = true; this.set({ busy: true, error: '', notice: '' }); const ticket = this.epoch;
    try {
      if (intent.kind === 'create') {
        let found: PlannerSummary;
        try { found = await briefPlannerApi.byKey(intent.request.client_request_key); }
        catch (error) {
          if (retry && error instanceof BriefPlannerError && error.code === 'NOT_FOUND') { await this.dispatch(intent, ticket); return; }
          throw error;
        }
        if (!this.matches(found, intent)) throw new Error('binding');
        const task = await briefPlannerApi.get(found.id);
        if (task.id !== found.id || !this.matches(task, intent) || task.client_request_key !== intent.request.client_request_key) throw new Error('binding');
        this.clear(intent); this.accept(task, ticket);
        if (ticket === this.epoch) { this.set({ notice: '已按原创建键查回，仍使用原冻结版本。' }); await this.loadEvents(); }
      } else if (retry) await this.dispatch(intent, ticket);
      else {
        const task = await briefPlannerApi.get(intent.task_id);
        if (task.id !== intent.task_id) throw new Error('binding');
        this.accept(task, ticket);
        if (ticket === this.epoch) { this.set({ notice: '已读取当前任务；查询不能确认原动作键。请按原键确认，原动作和原版本号保持不变。' }); await this.loadEvents(); }
      }
    } catch (error) { if (ticket === this.epoch) this.report(error); }
    finally { this.locked = false; this.set({ busy: false }); }
  }
}
