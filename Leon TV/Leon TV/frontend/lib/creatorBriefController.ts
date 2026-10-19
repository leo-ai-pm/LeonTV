import { creatorBriefApi, CreatorBriefError, readCreatorSummary } from '@/lib/creatorBriefApi';
import { EMPTY_CREATOR_FIELDS, type CreatorBriefDetail, type CreatorBriefSummary, type CreatorBriefWriteResult, type CreatorContent, type CreatorCreateRequest, type CreatorEditorFields, type CreatorUpdateRequest } from '@/lib/creatorBriefTypes';

type StoragePort = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
type WriteBody = CreatorCreateRequest | CreatorUpdateRequest;
export interface CreatorIntent {
  version: 1; kind: 'create' | 'update'; key: string; content_hash: string; brief_id?: string; expected_revision?: number;
}
export interface CreatorBriefState {
  fields: CreatorEditorFields; document: CreatorBriefDetail | null; conflict: CreatorBriefDetail | null;
  briefs: CreatorBriefSummary[]; nextOffset: number | null; pending: CreatorIntent | null;
  loading: boolean; listLoading: boolean; saving: boolean; dirty: boolean; conflictDetected: boolean;
  error: string; notice: string; fieldErrors: Partial<Record<keyof CreatorEditorFields, string>>;
}
const INITIAL: CreatorBriefState = { fields: EMPTY_CREATOR_FIELDS, document: null, conflict: null, briefs: [], nextOffset: null, pending: null,
  loading: true, listLoading: true, saving: false, dirty: false, conflictDetected: false, error: '', notice: '', fieldErrors: {} };

export function normalizeCreatorBudget(value: string): string | null {
  const raw = value.trim();
  if (!raw) return null;
  if (!/^\d+(?:\.\d{1,2})?$/.test(raw)) throw new Error('预算请填写非负金额，最多两位小数；留空表示待定。');
  const [whole, fraction = ''] = raw.split('.');
  if (Number(whole) > 1000000 || Number(whole) === 1000000 && Number(fraction) > 0) throw new Error('预算草稿上限为 1000000.00 元。');
  return `${Number(whole)}.${fraction.padEnd(2, '0')}`;
}
export function validateCreatorFields(fields: CreatorEditorFields): Partial<Record<keyof CreatorEditorFields, string>> {
  const errors: Partial<Record<keyof CreatorEditorFields, string>> = {};
  if (Array.from(fields.title.trim()).length > 120) errors.title = '作品名称最多 120 字符。';
  if (!fields.script.trim()) errors.script = '请填写剧本或故事梗概。';
  else if (Array.from(fields.script).length > 200000) errors.script = '剧本最多 200000 字符，请缩小本次制作范围。';
  if (Array.from(fields.requirements).length > 20000) errors.requirements = '补充要求最多 20000 字符。';
  try { normalizeCreatorBudget(fields.budgetText); } catch (error) { errors.budgetText = error instanceof Error ? error.message : '预算格式不正确。'; }
  return errors;
}
function content(fields: CreatorEditorFields): CreatorContent {
  return { title: fields.title.trim() || '未命名创作简报', script: fields.script, requirements: fields.requirements,
    production: { duration_seconds: 15, ratio: '9:16', style: 'realistic_cinematic' }, budget_cny: normalizeCreatorBudget(fields.budgetText) };
}
function fieldsFrom(detail: CreatorBriefDetail): CreatorEditorFields {
  return { title: detail.title, script: detail.script, requirements: detail.requirements, budgetText: detail.budget_cny ?? '' };
}
function sameFields(a: CreatorEditorFields, b: CreatorEditorFields): boolean { return JSON.stringify(a) === JSON.stringify(b); }
function sameContent(a: CreatorEditorFields, b: CreatorEditorFields): boolean {
  try { return JSON.stringify(content(a)) === JSON.stringify(content(b)); }
  catch { return false; } // A partially typed budget must not make a successful receipt erase input.
}
async function hashContent(value: CreatorContent): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(value)));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}
function mergeSummaries(previous: CreatorBriefSummary[], incoming: CreatorBriefSummary[]): CreatorBriefSummary[] {
  const map = new Map(previous.map(brief => [brief.id, brief]));
  for (const brief of incoming) if (!map.has(brief.id) || map.get(brief.id)!.revision <= brief.revision) map.set(brief.id, brief);
  // Project only safe summaries, even if the source was a write/detail response.
  return Array.from(map.values()).map(readCreatorSummary)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

/** Private text stays in memory. Durable recovery stores only random keys, IDs and a digest. */
export class CreatorBriefController {
  readonly storageKey = 'leontv.creator.pending.v1';
  readonly intentEpochKey = 'leontv.creator.last-intent.v1';
  private state: CreatorBriefState = INITIAL;
  private listeners = new Set<() => void>();
  private epoch = 0;
  private listEpoch = 0;
  private editSequence = 0;
  private selectionId: string | null = null;
  private lock = false;
  private bodies = new Map<string, WriteBody>();
  constructor(private storage: () => StoragePort) {}
  getSnapshot = (): CreatorBriefState => this.state;
  getServerSnapshot = (): CreatorBriefState => INITIAL;
  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private set(patch: Partial<CreatorBriefState>): void { this.state = { ...this.state, ...patch }; for (const listener of this.listeners) listener(); }
  invalidate(): void { this.epoch++; this.listEpoch++; }
  report(error: unknown): void { this.set({ error: error instanceof Error ? error.message : '操作尚未完成，输入仍保留。' }); }
  edit(patch: Partial<CreatorEditorFields>): void {
    this.editSequence++;
    const fields = { ...this.state.fields, ...patch };
    this.set({ fields, dirty: !sameFields(fields, this.state.document ? fieldsFrom(this.state.document) : EMPTY_CREATOR_FIELDS), fieldErrors: {}, notice: '' });
  }
  private readIntent(): CreatorIntent | null {
    let raw: string | null;
    try { raw = this.storage().getItem(this.storageKey); } catch { throw new Error('无法读取本地恢复键，已阻止新保存。请允许本站存储后重试。'); }
    if (raw === null) return null;
    let parsed: unknown;
    try { parsed = JSON.parse(raw); } catch { throw new Error('本地保存请求记录损坏，请先核查原简报，不能换键重建。'); }
    if (!parsed || typeof parsed !== 'object') throw new Error('本地保存请求记录不正确。');
    const intent = parsed as CreatorIntent;
    if (intent.version !== 1 || !['create', 'update'].includes(intent.kind) || typeof intent.key !== 'string' || !/^[A-Za-z0-9_-]{8,128}$/.test(intent.key) ||
        typeof intent.content_hash !== 'string' || !/^[a-f0-9]{64}$/.test(intent.content_hash) || intent.kind === 'update' && (!intent.brief_id || !/^[a-f0-9]{32}$/.test(intent.brief_id) || !Number.isInteger(intent.expected_revision) || intent.expected_revision! < 1)) throw new Error('本地保存请求记录不匹配，请先核查原记录。');
    return intent;
  }
  private persist(intent: CreatorIntent): void {
    try { this.storage().setItem(this.storageKey, JSON.stringify(intent)); }
    catch { throw new Error('无法持久保存原请求键，已阻止提交。正文仍保留在编辑器。'); }
    this.set({ pending: intent });
  }
  private clearIntent(key: string): void {
    if (this.readIntent()?.key !== key) return;
    try { this.storage().setItem(this.intentEpochKey, key); this.storage().removeItem(this.storageKey); this.bodies.delete(key); this.set({ pending: null }); }
    catch { this.set({ notice: '服务器已确认保存，但浏览器未能清理恢复键。原键仍保留，可继续查回。' }); }
  }
  async initialize(id: string | null): Promise<void> {
    try {
      const pending = this.readIntent(); this.set({ pending });
      void this.loadList();
      if (id && this.state.document?.id === id) { this.set({ loading: false }); return; }
      if (id) { await this.open(id); return; }
      if (pending?.kind === 'create') { await this.lookupCreation(pending); return; }
      if (pending?.brief_id) { await this.open(pending.brief_id); this.set({ notice: '上次更新结果待确认。请查询后按原键确认保存，不会自动创建新版本。' }); return; }
      this.set({ loading: false });
    } catch (error) { this.set({ loading: false, error: error instanceof Error ? error.message : '简报读取失败。' }); }
  }
  async loadList(more = false): Promise<void> {
    const ticket = ++this.listEpoch;
    this.set({ listLoading: true });
    try {
      const result = await creatorBriefApi.list(more ? this.state.nextOffset ?? 0 : 0);
      if (ticket === this.listEpoch) this.set({ briefs: mergeSummaries(this.state.briefs, result.briefs), nextOffset: result.next_offset });
    } catch (error) { if (ticket === this.listEpoch) this.set({ error: error instanceof Error ? error.message : '简报列表读取失败。' }); }
    finally { if (ticket === this.listEpoch) this.set({ listLoading: false }); }
  }
  async open(id: string): Promise<CreatorBriefDetail | null> {
    const ticket = ++this.epoch, sequence = this.editSequence;
    this.selectionId = id;
    this.set({ loading: true, error: '', notice: '', conflict: null, conflictDetected: false });
    try {
      const detail = await creatorBriefApi.get(id);
      if (ticket !== this.epoch) return null;
      if (sequence !== this.editSequence) { this.selectionId = this.state.document?.id ?? null; this.set({ notice: '读取期间出现新的本地编辑，已保留原输入。请保存或明确放弃后再打开。' }); return null; }
      this.set({ document: detail, fields: fieldsFrom(detail), dirty: false, conflict: null,
        briefs: mergeSummaries(this.state.briefs, [detail]), fieldErrors: {} });
      return detail;
    } catch (error) { if (ticket === this.epoch) this.set({ error: error instanceof Error ? error.message : '正文读取失败，已有编辑未改变。' }); return null; }
    finally { if (ticket === this.epoch) this.set({ loading: false }); }
  }
  newDraft(): void {
    if (this.state.saving || this.readIntent()) throw new Error('原保存结果尚未确认，请先查回，不能换键新建。');
    this.epoch++; this.editSequence++; this.selectionId = null;
    this.set({ fields: { ...EMPTY_CREATOR_FIELDS }, document: null, conflict: null, conflictDetected: false, dirty: false, loading: false, error: '', notice: '', fieldErrors: {} });
  }
  private async lookupCreation(intent: CreatorIntent): Promise<CreatorBriefDetail | null> {
    const ticket = ++this.epoch, sequence = this.editSequence, selected = this.selectionId;
    this.set({ loading: true, pending: intent, error: '' });
    try {
      const summary = await creatorBriefApi.byKey(intent.key);
      const detail = await creatorBriefApi.get(summary.id);
      if (ticket !== this.epoch) return null;
      this.clearIntent(intent.key);
      if (selected && selected !== detail.id) {
        this.set({ briefs: mergeSummaries(this.state.briefs, [summary]), notice: '原创建已查回，当前选中的简报与未保存编辑未改变。' });
        return null;
      }
      this.selectionId = detail.id;
      const preserve = sequence !== this.editSequence || this.state.dirty && !sameContent(this.state.fields, fieldsFrom(detail));
      this.set({ document: detail, fields: preserve ? this.state.fields : fieldsFrom(detail),
        dirty: preserve && !sameFields(this.state.fields, fieldsFrom(detail)), conflict: preserve && detail.revision > 1 ? detail : null, conflictDetected: preserve && detail.revision > 1, briefs: mergeSummaries(this.state.briefs, [summary]),
        notice: preserve ? '原创建已查回，读取期间的本地编辑仍保留，尚未保存为新版本。' : '已按原请求键查回已保存的简报，未重复创建。' });
      return detail;
    } catch (error) {
      if (ticket === this.epoch) this.set({ error: error instanceof CreatorBriefError && error.status === 404 ? '暂未查到原保存记录。原键保留，可稍后重查；不会换键新建。' : error instanceof Error ? error.message : '原保存查询失败。' });
      return null;
    } finally { if (ticket === this.epoch) this.set({ loading: false }); }
  }
  private acknowledge(result: CreatorBriefWriteResult, intent: CreatorIntent, ticket: number, sequence: number, submitted: CreatorEditorFields): CreatorBriefDetail | null {
    this.clearIntent(intent.key);
    this.set({ briefs: mergeSummaries(this.state.briefs, [result.brief]) });
    if (ticket !== this.epoch || intent.kind === 'update' && this.selectionId !== intent.brief_id || intent.kind === 'create' && this.selectionId !== null && this.selectionId !== result.brief.id) return null;
    const detail = result.brief;
    const newerContent = detail.revision > result.applied_revision && !sameContent(fieldsFrom(detail), submitted);
    const preserve = sequence !== this.editSequence || newerContent || !sameContent(this.state.fields, submitted);
    this.selectionId = detail.id;
    this.set({ document: detail, fields: preserve ? this.state.fields : fieldsFrom(detail),
      dirty: preserve && !sameFields(this.state.fields, fieldsFrom(detail)), conflict: newerContent ? detail : null, conflictDetected: newerContent,
      notice: newerContent ? `原保存已确认写入 v${result.applied_revision}，但服务器已有 v${detail.revision}。你的输入保留，请核对再合并。`
        : preserve ? `已保存 v${detail.revision}；保存期间继续输入的修改仍未保存。` : `已保存 v${detail.revision}，未授权、未开始制作。`, error: '' });
    return detail;
  }
  private async handleFailure(error: unknown, intent: CreatorIntent, ticket: number): Promise<void> {
    if (error instanceof CreatorBriefError && error.code === 'REVISION_CONFLICT' && intent.brief_id) {
      this.clearIntent(intent.key);
      if (ticket === this.epoch) this.set({ conflictDetected: true });
      try {
        const latest = await creatorBriefApi.get(intent.brief_id);
        if (ticket === this.epoch && this.selectionId === intent.brief_id) this.set({ conflict: latest, briefs: mergeSummaries(this.state.briefs, [latest]) });
      } catch { /* Keep both the original conflict and all unsaved input. */ }
    } else if (error instanceof CreatorBriefError && error.status === 422) this.clearIntent(intent.key);
    if (ticket === this.epoch) this.set({ error: error instanceof Error ? error.message : '保存结果尚未确认，请查回原请求。' });
  }
  async save(): Promise<CreatorBriefDetail | null> {
    if (this.lock) throw new Error('正在确认上一保存，请勿重复提交。');
    const fieldErrors = validateCreatorFields(this.state.fields);
    this.set({ fieldErrors, error: '' });
    if (Object.keys(fieldErrors).length) return null;
    const pending = this.readIntent();
    if (pending) { this.set({ pending }); throw new Error('存在尚未确认的原请求，请先查回。'); }
    if (this.state.conflictDetected || this.state.document && !this.state.document.is_current) throw new Error('请先核对当前版本，再显式保存。');
    const intentEpoch = this.storage().getItem(this.intentEpochKey);
    this.lock = true; this.set({ saving: true, notice: '' });
    const ticket = this.epoch, sequence = this.editSequence, submitted = { ...this.state.fields }, current = this.state.document;
    let intent: CreatorIntent | undefined;
    try {
      const bodyContent = content(submitted), key = crypto.randomUUID(), digest = await hashContent(bodyContent);
      const competing = this.readIntent();
      if (competing) { this.set({ pending: competing }); throw new Error('其他窗口已保存请求键，请先查回原请求。'); }
      if (this.storage().getItem(this.intentEpochKey) !== intentEpoch) throw new Error('其他窗口已确认保存。请刷新列表核对已保存简报，当前输入仍保留。');
      intent = { version: 1, kind: current ? 'update' : 'create', key, content_hash: digest,
        ...(current ? { brief_id: current.id, expected_revision: current.revision } : {}) };
      const body: WriteBody = current ? { client_update_key: key, expected_revision: current.revision, ...bodyContent } : { client_request_key: key, ...bodyContent };
      this.persist(intent); this.bodies.set(key, body);
      const result = current ? await creatorBriefApi.update(current.id, body as CreatorUpdateRequest) : await creatorBriefApi.create(body as CreatorCreateRequest);
      return this.acknowledge(result, intent, ticket, sequence, submitted);
    } catch (error) { if (intent) await this.handleFailure(error, intent, ticket); else if (ticket === this.epoch) this.set({ error: error instanceof Error ? error.message : '保存未发出。' }); throw error; }
    finally { this.lock = false; this.set({ saving: false }); }
  }
  async recover(retry = false): Promise<CreatorBriefDetail | null> {
    if (this.lock) throw new Error('正在确认上一保存，请稍后重查。');
    const intent = this.readIntent();
    if (!intent) return this.state.document ? this.refresh() : null;
    if (intent.kind === 'create' && !retry) return this.lookupCreation(intent);
    this.lock = true; this.set({ saving: retry, loading: !retry, pending: intent, error: '' });
    const ticket = this.epoch, sequence = this.editSequence;
    try {
      if (intent.kind === 'create') {
        try {
          const summary = await creatorBriefApi.byKey(intent.key), detail = await creatorBriefApi.get(summary.id);
          const submitted = this.bodies.get(intent.key);
          return this.acknowledge({ brief: detail, replayed: true, applied_revision: 1 }, intent, ticket, sequence,
            submitted ? { title: submitted.title, script: submitted.script, requirements: submitted.requirements, budgetText: submitted.budget_cny ?? '' } : fieldsFrom(detail));
        } catch (error) { if (!(error instanceof CreatorBriefError && error.status === 404)) throw error; }
      } else if (intent.brief_id) {
        const current = await creatorBriefApi.get(intent.brief_id);
        this.set({ briefs: mergeSummaries(this.state.briefs, [current]) });
        if (!retry) { if (ticket === this.epoch) this.set({ notice: '已查回当前版本。原更新是否成功仍需按原键确认，不会自动重提。' }); return current; }
        if (!this.bodies.has(intent.key) && current.revision > intent.expected_revision!) {
          const candidate = current.revision === intent.expected_revision! + 1 ? current : await creatorBriefApi.get(current.id, intent.expected_revision! + 1);
          if (await hashContent(content(fieldsFrom(candidate))) === intent.content_hash) this.bodies.set(intent.key, {
            client_update_key: intent.key, expected_revision: intent.expected_revision!, ...content(fieldsFrom(candidate)),
          });
        }
      }
      if (!retry) return null;
      let body = this.bodies.get(intent.key);
      if (!body) {
        const value = content(this.state.fields);
        if (await hashContent(value) !== intent.content_hash) throw new Error('原稿未保存在浏览器。当前输入与原请求不一致，请保留输入并重查；只有重新填回原内容后才能按原键重试。');
        body = intent.kind === 'create' ? { client_request_key: intent.key, ...value } : { client_update_key: intent.key, expected_revision: intent.expected_revision!, ...value };
      }
      const result = intent.kind === 'create' ? await creatorBriefApi.create(body as CreatorCreateRequest) : await creatorBriefApi.update(intent.brief_id!, body as CreatorUpdateRequest);
      return this.acknowledge(result, intent, ticket, sequence, { title: body.title, script: body.script, requirements: body.requirements, budgetText: body.budget_cny ?? '' });
    } catch (error) { await this.handleFailure(error, intent, ticket); throw error; }
    finally { this.lock = false; this.set({ loading: false, saving: false }); }
  }
  async refresh(): Promise<CreatorBriefDetail | null> {
    const id = this.state.document?.id;
    if (!id) return null;
    const ticket = this.epoch;
    try {
      const detail = await creatorBriefApi.get(id);
      if (ticket !== this.epoch || this.selectionId !== id) return null;
      if (detail.revision < (this.state.document?.revision ?? 0) || detail.revision < (this.state.conflict?.revision ?? 0)) return null;
      if (this.state.dirty || this.state.conflictDetected) this.set({ conflict: detail.revision !== this.state.document?.revision || this.state.conflictDetected ? detail : this.state.conflict,
        conflictDetected: detail.revision !== this.state.document?.revision || this.state.conflictDetected,
        notice: '已读取服务器版本，你的未保存输入保持不变。', briefs: mergeSummaries(this.state.briefs, [detail]) });
      else this.set({ document: detail, fields: fieldsFrom(detail), briefs: mergeSummaries(this.state.briefs, [detail]), error: '' });
      return detail;
    } catch (error) { if (ticket === this.epoch) this.set({ error: error instanceof Error ? error.message : '刷新失败，当前输入仍保留。' }); return null; }
  }
  rebase(keepLocal: boolean): void {
    const latest = this.state.conflict;
    if (!latest) return;
    this.editSequence++;
    const fields = keepLocal ? this.state.fields : fieldsFrom(latest);
    this.selectionId = latest.id;
    this.set({ document: latest, fields, conflict: null, conflictDetected: false, dirty: !sameFields(fields, fieldsFrom(latest)), error: '',
      notice: keepLocal ? `已以 v${latest.revision} 为基础保留你的修改，尚未保存；请核对后显式保存新版本。` : `已载入服务器 v${latest.revision}。` });
  }
}
