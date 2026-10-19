import { harnessApi, HarnessApiError, readHarnessRun } from '@/lib/harnessApi';
import { getStudioProject } from '@/lib/studioApi';
import type { HarnessAction, HarnessActionRequest, HarnessCreateRequest, HarnessReviewRequest, HarnessRun, HarnessScenario } from '@/lib/harnessTypes';

type PendingOperation = { kind: 'action'; request: HarnessActionRequest } | { kind: 'review'; request: HarnessReviewRequest };
export interface HarnessCheckpoint {
  version: 1; project_id: string; client_request_key: string; run_id?: string;
  create_request?: HarnessCreateRequest; pending?: PendingOperation;
  last_snapshot?: HarnessRun;
}
type CheckpointStorage = Pick<Storage, 'getItem' | 'setItem'>;

export function mergeHarnessRuns(previous: HarnessRun[], incoming: HarnessRun[]): HarnessRun[] {
  const runs = new Map(previous.map(run => [run.id, run]));
  for (const run of incoming) {
    if (!runs.has(run.id) || runs.get(run.id)!.revision <= run.revision) runs.set(run.id, run);
  }
  return Array.from(runs.values()).sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export function canReviewHarness(run: HarnessRun): boolean {
  if (run.mode !== 'test' || !run.test_mode || run.status !== 'waiting_human_review' || !run.manifest?.valid || run.budget.unresolved_operations !== 0) return false;
  const final = run.artifacts.find(artifact => artifact.id === run.manifest?.final_artifact_id && artifact.version === run.manifest.final_version);
  if (!final || final.stale || final.generation_status !== 'succeeded' || final.quality_status !== 'accepted' || final.sha256 !== run.manifest.final_sha256) return false;
  return run.artifacts.every(artifact => !artifact.stale && artifact.generation_status === 'succeeded' && artifact.quality_status === 'accepted' &&
    artifact.dependencies.every(dependency => run.artifacts.some(upstream => upstream.id === dependency.artifact_id && upstream.version === dependency.version && upstream.sha256 === dependency.sha256 && !upstream.stale)) &&
    artifact.required_scopes.length > 0 && artifact.required_scopes.every(scope => run.reviews.some(review => review.artifact_id === artifact.id && review.artifact_version === artifact.version &&
      review.artifact_sha256 === artifact.sha256 && review.valid && review.mode === 'test' && review.evidence_type === 'fixture' && review.status === 'accepted' && review.scopes[scope] === 'accepted')));
}

/** Only explicit button handlers call mutations. restore/refresh never create or resume. */
export class HarnessRunController {
  private busy = false;
  readonly storageKey: string;
  constructor(readonly projectId: string, private storage: () => CheckpointStorage) { this.storageKey = `leontv.harness.project.${projectId}`; }

  checkpoint(): HarnessCheckpoint | null {
    const raw = this.storage().getItem(this.storageKey);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') throw new Error('本地运行记录损坏，请先核查原运行，不能重新提交。');
    const value = parsed as HarnessCheckpoint;
    if (value.version !== 1 || value.project_id !== this.projectId || typeof value.client_request_key !== 'string' || !value.client_request_key) throw new Error('本地运行记录不匹配，请先核查原运行。');
    if (value.last_snapshot) this.ensureProject(readHarnessRun(value.last_snapshot));
    return value;
  }

  private save(checkpoint: HarnessCheckpoint): void {
    try { this.storage().setItem(this.storageKey, JSON.stringify(checkpoint)); }
    catch { throw new Error('无法持久保存运行请求键，已阻止提交。请允许本站存储后重试。'); }
  }
  private ensureProject(run: HarnessRun): HarnessRun {
    if (run.project_id !== this.projectId) throw new Error('运行与当前项目不匹配，已停止操作。');
    return run;
  }
  track(run: HarnessRun): HarnessRun {
    this.ensureProject(run);
    const previous = this.checkpoint();
    this.save(previous?.client_request_key === run.client_request_key
      ? { ...previous, run_id: run.id, last_snapshot: run }
      : { version: 1, project_id: this.projectId, client_request_key: run.client_request_key, run_id: run.id, last_snapshot: run });
    return run;
  }
  private result(run: HarnessRun, expectedKey: string): HarnessRun {
    this.ensureProject(run);
    const current = this.checkpoint();
    if (current?.client_request_key === expectedKey && run.client_request_key === expectedKey &&
        (current.last_snapshot?.id !== run.id || current.last_snapshot.revision <= run.revision)) {
      this.save({ ...current, run_id: run.id, last_snapshot: run });
    }
    return run;
  }
  async restore(): Promise<HarnessRun | null> {
    const checkpoint = this.checkpoint();
    if (checkpoint) {
      try {
        return this.result(await (checkpoint.run_id ? harnessApi.get(checkpoint.run_id) : harnessApi.byKey(checkpoint.client_request_key)), checkpoint.client_request_key);
      } catch (error) {
        if (error instanceof HarnessApiError && error.status === 404 && !checkpoint.run_id) return null;
        throw error;
      }
    }
    const runs = await harnessApi.list(this.projectId);
    if (!runs[0]) return null;
    if (!this.checkpoint()) this.track(runs[0]);
    return runs[0];
  }
  async refresh(runId: string): Promise<HarnessRun> {
    const run = this.ensureProject(await harnessApi.get(runId));
    return this.result(run, run.client_request_key);
  }

  private async exclusive<T>(work: () => Promise<T>): Promise<T> {
    if (this.busy) throw new Error('上一操作尚未确认，请先查回原结果。');
    this.busy = true;
    try { return await work(); } finally { this.busy = false; }
  }

  async start(scenario: HarnessScenario): Promise<HarnessRun> {
    return this.exclusive(async () => {
      const previous = this.checkpoint();
      if (previous && (!previous.run_id || previous.pending)) throw new Error('存在尚未确认的原请求，请先查回，不能新建。');
      const project = await getStudioProject(this.projectId);
      const snapshot = JSON.stringify({ id: project.id, revision: project.revision, nodes: project.nodes, edges: project.edges });
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(snapshot));
      const recordDigest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(project.id));
      const request: HarnessCreateRequest = {
        mode: 'test', client_request_key: crypto.randomUUID(), project_id: this.projectId,
        source: { hash: Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join(''),
          record_ids: [`studio-${Array.from(new Uint8Array(recordDigest), value => value.toString(16).padStart(2, '0')).join('')}`] },
        production: { ratio: '9:16', duration_ms: 15000 }, authorization_ref: 'test-zero-paid-v1', scenario,
      };
      if (this.checkpoint()?.client_request_key !== previous?.client_request_key) throw new Error('另一个窗口已更新运行请求，请先查回，不能重复创建。');
      // The key and exact body must survive a refresh before the first POST.
      this.save({ version: 1, project_id: this.projectId, client_request_key: request.client_request_key, create_request: request, last_snapshot: previous?.last_snapshot });
      return this.result(await harnessApi.create(request), request.client_request_key);
    });
  }

  async retryCreation(): Promise<HarnessRun> {
    return this.exclusive(async () => {
      const checkpoint = this.checkpoint();
      if (!checkpoint?.create_request || checkpoint.run_id) throw new Error('没有待查回的创建请求。');
      try { return this.result(await harnessApi.byKey(checkpoint.client_request_key), checkpoint.client_request_key); }
      catch (error) { if (!(error instanceof HarnessApiError && error.status === 404)) throw error; }
      return this.result(await harnessApi.create(checkpoint.create_request), checkpoint.client_request_key);
    });
  }

  private async sendPending(runId: string, pending: PendingOperation): Promise<HarnessRun> {
    try {
      const run = this.ensureProject(await (pending.kind === 'action' ? harnessApi.action(runId, pending.request) : harnessApi.review(runId, pending.request)));
      const checkpoint = this.checkpoint();
      if (checkpoint?.run_id === runId && JSON.stringify(checkpoint.pending) === JSON.stringify(pending)) this.save({ ...checkpoint, pending: undefined, last_snapshot: run });
      return run;
    } catch (error) {
      // A definite conflict is not a lost response. The UI refreshes, never auto-resubmits.
      if (error instanceof HarnessApiError && error.status === 409) {
        const checkpoint = this.checkpoint();
        if (checkpoint?.run_id === runId && JSON.stringify(checkpoint.pending) === JSON.stringify(pending)) this.save({ ...checkpoint, pending: undefined });
      }
      throw error;
    }
  }

  async act(run: HarnessRun, action: HarnessAction, artifactId?: string): Promise<HarnessRun> {
    return this.exclusive(async () => {
      this.ensureProject(run);
      const checkpoint = this.checkpoint();
      if (!checkpoint || checkpoint.run_id !== run.id || checkpoint.pending) throw new Error('请先查回原操作结果。');
      if (!run.allowed_actions.includes(action)) throw new Error('当前运行不允许该操作，请刷新状态。');
      if (['resume', 'revise'].includes(action) && (run.budget.unresolved_operations > 0 || run.status === 'needs_reconciliation')) throw new Error('存在未确认的原操作，请先对账，不能续跑或改版。');
      const request: HarnessActionRequest = { mode: 'test', client_action_key: crypto.randomUUID(), expected_revision: run.revision, action, ...(action === 'revise' ? { artifact_id: artifactId } : {}) };
      const pending: PendingOperation = { kind: 'action', request };
      this.save({ ...checkpoint, pending });
      return this.sendPending(run.id, pending);
    });
  }

  async review(run: HarnessRun, decision: 'accept' | 'return', feedback: string): Promise<HarnessRun> {
    return this.exclusive(async () => {
      this.ensureProject(run);
      if (!canReviewHarness(run) || !run.manifest) throw new Error('当前版本尚不具备测试终审条件，质量未知或失效时不能接受。');
      const checkpoint = this.checkpoint();
      if (!checkpoint || checkpoint.run_id !== run.id || checkpoint.pending) throw new Error('请先查回原操作结果。');
      const request: HarnessReviewRequest = { mode: 'test', client_review_key: crypto.randomUUID(), expected_revision: run.revision,
        manifest_version: run.manifest.version, final_sha256: run.manifest.final_sha256, decision, feedback };
      const pending: PendingOperation = { kind: 'review', request };
      this.save({ ...checkpoint, pending });
      return this.sendPending(run.id, pending);
    });
  }

  async retryPending(): Promise<HarnessRun> {
    return this.exclusive(async () => {
      const checkpoint = this.checkpoint();
      if (!checkpoint?.run_id || !checkpoint.pending) throw new Error('没有待确认的原操作。');
      await this.refresh(checkpoint.run_id);
      return this.sendPending(checkpoint.run_id, checkpoint.pending);
    });
  }
}

/** Reject responses from an old selection, an older revision, or an unmounted view. */
export class HarnessViewGuard {
  private epoch = 0;
  private runId: string | null = null;
  private revision = -1;
  begin(runId: string | null = null): number { this.epoch += 1; this.runId = runId; this.revision = -1; return this.epoch; }
  ticket(): number { return this.epoch; }
  current(ticket: number): boolean { return ticket === this.epoch; }
  invalidate(): void { this.epoch += 1; }
  accept(ticket: number, run: Pick<HarnessRun, 'id' | 'revision'>): boolean {
    if (!this.current(ticket) || (this.runId !== null && run.id !== this.runId) || run.revision < this.revision) return false;
    this.runId = run.id; this.revision = run.revision;
    return true;
  }
}
