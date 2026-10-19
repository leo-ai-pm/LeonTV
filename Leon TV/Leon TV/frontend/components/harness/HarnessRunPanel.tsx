'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, RefreshCw, Square } from 'lucide-react';
import HarnessPanelFrame from './HarnessPanelFrame';
import { harnessApi, HarnessApiError, harnessMediaUrl } from '@/lib/harnessApi';
import { canReviewHarness, HarnessRunController, HarnessViewGuard, mergeHarnessRuns, type HarnessCheckpoint } from '@/lib/harnessRunController';
import type { HarnessEvent, HarnessRun, HarnessScenario } from '@/lib/harnessTypes';
import { errorMessage } from '@/lib/http';

const SCENARIOS: Array<{ id: HarnessScenario; label: string }> = [
  { id: 'success', label: '正向运行验证' }, { id: 'repair', label: '一次局部返工' },
  { id: 'review_unknown', label: '质量未知门禁' }, { id: 'review_rejected', label: '质量未通过门禁' },
  { id: 'unknown_acceptance', label: '外部结果待对账' }, { id: 'zero_budget', label: '测试执行额度耗尽' },
  { id: 'resume_checkpoint', label: '停止与恢复检查点' },
];
const STATUS: Record<string, string> = {
  queued: '排队中', running: '运行验证中', blocked: '已阻塞', needs_reconciliation: '等待对账', stopped: '已停止',
  waiting_human_review: '等待测试终审', test_accepted: '测试接受已记录', test_returned: '测试退回已记录', failed: '运行失败',
};
function qualityLabel(status: string) { return status === 'accepted' ? '通过（测试）' : status === 'rejected' ? '未通过' : '未知'; }
function generationLabel(status: string) { return ({ pending: '待执行', submitting: '提交中', succeeded: '执行完成', unknown: '结果未知', failed: '执行失败' } as Record<string, string>)[status] || '状态未知'; }

export default function HarnessRunPanel({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const controller = useMemo(() => new HarnessRunController(projectId, () => window.localStorage), [projectId]);
  const guard = useRef(new HarnessViewGuard());
  const runIdRef = useRef<string | null>(null);
  const cursor = useRef(0);
  const lock = useRef(false);
  const [run, setRun] = useState<HarnessRun | null>(null);
  const [runs, setRuns] = useState<HarnessRun[]>([]);
  const [events, setEvents] = useState<HarnessEvent[]>([]);
  const [checkpoint, setCheckpoint] = useState<HarnessCheckpoint | null>(null);
  const [scenario, setScenario] = useState<HarnessScenario>('success');
  const [feedback, setFeedback] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [trusted, setTrusted] = useState(false);
  const [error, setError] = useState('');

  const publish = useCallback((next: HarnessRun, ticket: number) => {
    if (!guard.current.accept(ticket, next)) return false;
    if (runIdRef.current !== next.id) { runIdRef.current = next.id; cursor.current = 0; setEvents([]); setFeedback(''); }
    setRun(next); setTrusted(true);
    setRuns(previous => mergeHarnessRuns(previous, [next]));
    return true;
  }, []);

  const readEvents = useCallback(async (id: string, ticket: number) => {
    let more = true;
    for (let page = 0; more && page < 10; page++) {
      const result = await harnessApi.events(id, cursor.current);
      if (!guard.current.current(ticket) || runIdRef.current !== id) return;
      cursor.current = Math.max(cursor.current, result.next_sequence);
      setEvents(previous => Array.from(new Map([...previous, ...result.events].map(event => [event.sequence, event])).values()).sort((a, b) => a.sequence - b.sequence).slice(-500));
      more = result.has_more;
    }
  }, []);

  const loadView = useCallback(async () => {
    const ticket = guard.current.begin();
    await Promise.resolve();
    if (!guard.current.current(ticket)) return;
    setLoading(true); setError(''); setTrusted(false);
    try {
      const stored = controller.checkpoint();
      setCheckpoint(stored);
      if (stored?.last_snapshot) { setRun(stored.last_snapshot); runIdRef.current = stored.last_snapshot.id; }
      const restored = await controller.restore();
      if (!guard.current.current(ticket)) return;
      setCheckpoint(controller.checkpoint());
      if (restored) { publish(restored, ticket); await readEvents(restored.id, ticket); }
      else setTrusted(true);
      const list = await harnessApi.list(projectId);
      if (guard.current.current(ticket)) setRuns(previous => mergeHarnessRuns(previous, list));
    } catch (cause) {
      if (guard.current.current(ticket)) setError(errorMessage(cause, '运行读取失败，请重查原请求。'));
    } finally { if (guard.current.current(ticket)) setLoading(false); }
  }, [controller, projectId, publish, readEvents]);

  useEffect(() => {
    // Recover after the modal opens; cancel a queued recovery if it closes first.
    const timer = window.setTimeout(() => { void loadView(); }, 0);
    const viewGuard = guard.current;
    return () => { window.clearTimeout(timer); viewGuard.invalidate(); };
  }, [loadView]);

  useEffect(() => {
    if (!run || !['queued', 'running'].includes(run.status)) return;
    const id = run.id;
    const ticket = guard.current.ticket();
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await controller.refresh(id);
        if (!active || !guard.current.current(ticket)) return;
        if (publish(next, ticket)) await readEvents(id, ticket);
      } catch (cause) { if (active && guard.current.current(ticket)) { setTrusted(false); setError(errorMessage(cause, '连接中断，运行可能仍在继续，请查询原运行。')); } }
      finally { if (active && guard.current.current(ticket)) timer = setTimeout(poll, document.hidden ? 10000 : 2500); }
    };
    timer = setTimeout(poll, 1500);
    return () => { active = false; clearTimeout(timer); };
  }, [run, controller, publish, readEvents]);

  const perform = async (work: () => Promise<HarnessRun>, newSelection = false) => {
    if (lock.current) return;
    lock.current = true;
    const ticket = newSelection ? guard.current.begin() : guard.current.ticket();
    setBusy(true); setError('');
    try {
      const next = await work();
      if (!guard.current.current(ticket)) return;
      if (publish(next, ticket)) await readEvents(next.id, ticket);
      const list = await harnessApi.list(projectId);
      if (guard.current.current(ticket)) setRuns(previous => mergeHarnessRuns(previous, list));
    } catch (cause) {
      if (!guard.current.current(ticket)) return;
      setError(errorMessage(cause, '提交结果尚未确认，请查回原请求。'));
      if (cause instanceof HarnessApiError && cause.status === 409 && run) {
        try { publish(await controller.refresh(run.id), ticket); } catch { /* Preserve the last snapshot and original conflict. */ }
      }
    } finally {
      lock.current = false;
      if (guard.current.current(ticket)) {
        setBusy(false);
        try { setCheckpoint(controller.checkpoint()); } catch (cause) { setError(errorMessage(cause)); setTrusted(false); }
      }
    }
  };

  const pendingCreate = Boolean(checkpoint && !checkpoint.run_id);
  const pending = Boolean(pendingCreate || checkpoint?.pending);
  const currentSnapshot = Boolean(run && checkpoint?.run_id === run.id && checkpoint.client_request_key === run.client_request_key);
  const canOperate = trusted && currentSnapshot && !busy && !loading && !pending;
  const unresolved = Boolean(run && run.budget.unresolved_operations > 0);
  const canCreate = trusted && !loading && !busy && !pending && !unresolved && (!run || ['test_accepted', 'test_returned', 'failed', 'stopped'].includes(run.status));
  const canReview = Boolean(run && canOperate && canReviewHarness(run));
  const final = run?.manifest ? run.artifacts.find(artifact => artifact.id === run.manifest?.final_artifact_id && artifact.version === run.manifest.final_version) : undefined;

  return (
    <HarnessPanelFrame onClose={onClose}>
      <div className="harness-toolbar">
        <label>验证情境<select value={scenario} onChange={event => setScenario(event.target.value as HarnessScenario)} disabled={busy} className="studio-field-input">{SCENARIOS.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>
        <button type="button" className="studio-save-button" disabled={!canCreate} onClick={() => void perform(() => controller.start(scenario), true)}>{busy ? <Loader2 size={14} className="animate-spin" /> : null}{run ? '新一轮验证' : '创建测试运行'}</button>
        <button type="button" className="studio-outline-button" disabled={busy || loading} onClick={() => void loadView()}><RefreshCw size={14} /> 查回 / 刷新</button>
      </div>
      <p className="harness-muted">读取已保存画布的摘要与稳定项目标识；9:16 · 15 秒测试规格。不会发送画布正文或调用模型。</p>
      {loading && <p role="status" className="harness-message"><Loader2 size={15} className="animate-spin" /> 正在读取原运行…</p>}
      {error && <p role="alert" className="harness-error">{error} 原请求键和已有快照均保留；请查回后再操作。</p>}
      {pending && <div role="status" className="harness-pending"><strong>原请求结果待确认</strong><p>刷新不会重新创建运行。请先查回；重试只使用已保存的原请求键与原参数。</p><button type="button" className="studio-outline-button" disabled={busy || loading} onClick={() => void perform(() => pendingCreate ? controller.retryCreation() : controller.retryPending(), pendingCreate)}>{pendingCreate ? '按原键查回 / 重试提交' : '查询后重试原操作'}</button></div>}
      {runs.length > 0 && <label className="harness-history">项目运行<select className="studio-field-input" aria-label="选择项目运行" value={run?.id || ''} disabled={busy || loading || pending} onChange={event => {
        const selected = runs.find(item => item.id === event.target.value);
        if (!selected) return;
        const ticket = guard.current.begin(selected.id);
        setTrusted(false); setError('');
        void controller.refresh(selected.id).then(next => { if (guard.current.current(ticket)) { controller.track(next); setCheckpoint(controller.checkpoint()); publish(next, ticket); return readEvents(next.id, ticket); } }).catch(cause => { if (guard.current.current(ticket)) setError(errorMessage(cause)); });
      }}>{runs.map(item => <option key={item.id} value={item.id}>{STATUS[item.status] || '未知状态'} · {item.id.slice(0, 12)} · 测试</option>)}</select></label>}
      {!run && !loading && !pending && !error && <section className="harness-section harness-empty"><h3>这个项目尚无运行验证</h3><p>选择测试情境并创建运行，验证持久化、门禁和恢复。现有画布和六阶段流程继续保留。</p></section>}
      {run && <>
        <section className="harness-section">
          <div className="harness-section-title"><h3>{STATUS[run.status] || `未知状态：${run.status}`}</h3><span className="harness-tag">测试运行 · r{run.revision}</span></div>
          {(!trusted || !currentSnapshot) && <p className="harness-pending">当前为上次已确认快照，仅供查看；查回当前运行前不能提交动作或终审。</p>}
          <p className="harness-mono">{run.id}</p>
          {run.blocked_reason && <p className="harness-pending">阻塞原因：{run.blocked_reason}</p>}
          <div className="harness-actions">
            <button type="button" className="studio-outline-button" disabled={!canOperate || !run.allowed_actions.includes('stop')} onClick={() => void perform(() => controller.act(run, 'stop'))}><Square size={12} /> 停止运行</button>
            <button type="button" className="studio-outline-button" disabled={!canOperate || unresolved || !run.allowed_actions.includes('resume')} onClick={() => void perform(() => controller.act(run, 'resume'))}>恢复验证</button>
            <button type="button" className="studio-outline-button" disabled={!canOperate || !run.allowed_actions.includes('reconcile')} onClick={() => void perform(() => controller.act(run, 'reconcile'))}>查询原操作 / 对账</button>
          </div>
        </section>
        <section className="harness-section"><h3>计划与来源</h3><p className="harness-mono" title={run.source.hash}>来源摘要：{run.source.hash}</p><p className="harness-muted">来源记录：{run.source.record_ids.join('、')}</p><ol className="harness-plan">{run.plan.map(step => <li key={step.id}><strong>{step.id}</strong><span>{step.depends_on.length ? `依赖 ${step.depends_on.join('、')}` : '无上游依赖'}</span></li>)}</ol></section>
        <section className="harness-section"><h3>版本、依赖与质量</h3><p className="harness-muted">执行完成与质量判断分别记录。通过仅是测试证据，不代表真实作品质量。</p>{run.artifacts.map(artifact => <article className="harness-artifact" key={`${artifact.id}:${artifact.version}`}>
          <div className="harness-section-title"><strong>{artifact.id} · v{artifact.version}</strong><span className={`harness-quality harness-quality-${artifact.quality_status === 'accepted' ? 'accepted' : artifact.quality_status === 'rejected' ? 'rejected' : 'unknown'}`}>{qualityLabel(artifact.quality_status)}</span></div>
          <p>执行：{generationLabel(artifact.generation_status)}{artifact.stale ? ' · 已失效，需局部修复' : ''}</p>
          <p className="harness-muted">依赖：{artifact.dependencies.length ? artifact.dependencies.map(dep => `${dep.artifact_id} v${dep.version}`).join('、') : '无'} · 必需审查：{artifact.required_scopes.join('、') || '未声明'}</p>
          <details><summary>查看审查证据</summary>{run.reviews.filter(review => artifact.review_ids.includes(review.id)).map(review => <p key={review.id}>{qualityLabel(review.status)} · {review.valid ? '有效' : '已失效'} · {Object.entries(review.scopes).map(([scope, verdict]) => `${scope}：${qualityLabel(verdict)}`).join('、')} · {review.reason || review.rule_version}</p>)}</details>
          {['reference', 'shot'].includes(artifact.id) && <button type="button" className="harness-text-button" disabled={!canOperate || unresolved || !run.allowed_actions.includes('revise')} onClick={() => void perform(() => controller.act(run, 'revise', artifact.id))}>测试上游改版与依赖失效</button>}
        </article>)}</section>
        <section className="harness-section"><h3>预算与调用</h3><dl className="harness-budget"><div><dt>付费限额</dt><dd>{run.budget.currency} {run.budget.paid_limit}</dd></div><div><dt>已发生 / 已预留</dt><dd>{run.budget.paid_actual} / {run.budget.paid_reserved}</dd></div><div><dt>结果未知操作</dt><dd>{run.budget.unresolved_operations}</dd></div><div><dt>测试执行次数</dt><dd>{run.budget.fixture_used} / {run.budget.fixture_limit}</dd></div><div><dt>模型 / 付费调用</dt><dd>{run.metrics.model_http_calls ?? '未知'} / {run.metrics.paid_calls ?? '未知'}</dd></div><div><dt>局部返工 / 最终决定</dt><dd>{run.metrics.repair_count ?? '未知'} / {run.metrics.final_decision_count ?? '未知'}</dd></div></dl></section>
        <section className="harness-section"><h3>当前版本与测试终审</h3>{final?.media_url && <figure className="harness-preview"><video key={`${run.id}:${final.version}`} controls playsInline preload="metadata" src={harnessMediaUrl(run.id, final.id, final.version)} style={{ aspectRatio: run.production.ratio.replace(':', ' / ') }} /><figcaption>TEST 测试合成素材 · {run.production.ratio} · {run.production.duration_ms / 1000} 秒规格</figcaption><a href={harnessMediaUrl(run.id, final.id, final.version)} download>下载测试素材</a></figure>}
          {run.manifest ? <p className="harness-mono">manifest v{run.manifest.version} · final v{run.manifest.final_version}<br />{run.manifest.final_sha256}</p> : <p className="harness-muted">尚无可终审的当前版本。</p>}
          {run.final_review && <p role="status" className="harness-pending">已记录测试决定；这不是用户真实成片的最终接受。</p>}
          <label className="studio-field-label" htmlFor="harness-review-feedback">测试终审备注</label><textarea id="harness-review-feedback" className="studio-field-input" maxLength={1000} value={feedback} disabled={!canReview} onChange={event => setFeedback(event.target.value)} rows={2} />
          <div className="harness-actions"><button type="button" className="studio-save-button" disabled={!canReview} onClick={() => void perform(() => controller.review(run, 'accept', feedback))}>记录测试接受</button><button type="button" className="studio-outline-button" disabled={!canReview} onClick={() => void perform(() => controller.review(run, 'return', feedback))}>记录测试退回</button></div>
          {!canReview && <p className="harness-muted">仅当前版本、有效质量证据齐备且无未知操作时，可提交测试终审。版本冲突后须重新核对。</p>}
        </section>
        <section className="harness-section"><h3>持久事件</h3><p className="harness-muted">按序号读取与去重，不会因刷新或重连重新执行工具。</p>{events.length ? <ol className="harness-events">{events.map(event => <li key={event.sequence}><span>#{event.sequence}</span><div><strong>{event.type}</strong><p>{event.artifact_id ? `${event.artifact_id}${event.version ? ` v${event.version}` : ''} · ` : ''}{event.reason || event.quality_status || event.generation_status || ''}</p><time dateTime={event.timestamp}>{event.timestamp}</time></div></li>)}</ol> : <p className="harness-muted">尚未读取到事件。</p>}</section>
      </>}
    </HarnessPanelFrame>
  );
}
