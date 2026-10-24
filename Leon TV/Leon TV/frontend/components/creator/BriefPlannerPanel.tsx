'use client';

import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { ClipboardList, RefreshCw, ShieldCheck } from 'lucide-react';
import { BriefPlannerController, plannerGate } from '@/lib/briefPlannerController';
import type { CreatorBriefController } from '@/lib/creatorBriefController';
import type { PlannerScenario, PlannerStatus } from '@/lib/briefPlannerTypes';
import PlannerExecutionPanel from './PlannerExecutionPanel';
import PlannerCollaborationPanel from './PlannerCollaborationPanel';

const statusLabel: Record<PlannerStatus, string> = { awaiting_authorization: '待授权', ready: '离线验证待启动', queued: '离线验证排队中', running: '离线验证运行中', stopped: '已暂停', response_unknown: '模型响应待确认', plan_ready: '计划结构合格 · 质量未审核', blocked: '已阻断' };
const reasonLabel: Record<string, string> = {
  AUTHORIZATION_REQUIRED: '当前没有有效执行授权。任务已保存，未调用模型；预算草稿不构成授权。',
  STOPPED: '任务已暂停，已确认记录和未决信息仍保留。', RECOVERY_REQUIRES_RESUME: '已恢复持久记录，等待明确续行。',
  MODEL_RESPONSE_UNCONFIRMED: '有调用意图但没有确认回执，不能重调或强制重试。暂停也会保留这条未决信息。',
  REPAIR_LIMIT: '已达到修正次数上限，请核对安全事件和计划记录。', STEP_LIMIT: '已达到规划步数上限，任务已停止推进。', CONTEXT_LIMIT: '已达到上下文大小上限，任务已停止推进。',
  REVIEW_REQUIRES_REPAIR: '合成首审拒绝了当前测试计划，已准备有限返工提案；实际改稿与复核尚未运行。',
  REVIEW_NEEDS_INPUT: '合成首审缺少必要信息，不能判为合格；请查看定位问题与提案。', REVIEW_INVALID: '审阅格式或绑定无效，没有有效内容结论。',
};
const scenarios: Array<{ value: PlannerScenario; label: string }> = [
  { value: 'success', label: '合格计划（合成响应）' }, { value: 'repair', label: '坏计划后有限修正' }, { value: 'invalid_tool', label: '非法工具阻断' },
  { value: 'step_limit', label: '步数上限阻断' }, { value: 'response_unknown', label: '模拟响应未确认' },
  { value: 'review_accepted', label: '规划 → 首审接受（合成响应）' }, { value: 'review_rejected', label: '规划 → 首审拒绝与有限返工提案' },
  { value: 'review_unknown', label: '规划 → 首审缺证' }, { value: 'review_response_unknown', label: 'Reviewer 响应未确认' }, { value: 'review_invalid', label: 'Reviewer 非法报告阻断' },
];
const time = (value: string) => new Date(value).toLocaleString('zh-CN');

export default function BriefPlannerPanel({ briefController }: { briefController: CreatorBriefController }) {
  const brief = useSyncExternalStore(briefController.subscribe, briefController.getSnapshot, briefController.getServerSnapshot);
  const controller = useMemo(() => new BriefPlannerController(() => window.localStorage), []);
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getServerSnapshot);
  const [scenario, setScenario] = useState<PlannerScenario>('success');
  useEffect(() => { void controller.initialize(); return () => controller.invalidate(); }, [controller]);
  const task = state.task;
  const selectedId = task?.id, selectedStatus = task?.status;
  useEffect(() => {
    if (!selectedId || !selectedStatus || !['queued','running'].includes(selectedStatus) || state.error) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (!active) return;
      if (!document.hidden) await controller.refresh();
      if (active && !controller.getSnapshot().error) timer = setTimeout(poll, 2000);
    };
    timer = setTimeout(poll, 2000);
    return () => { active = false; clearTimeout(timer); };
  }, [controller, selectedId, selectedStatus, state.error]);
  const gate = plannerGate(brief);
  const cannotCreate = Boolean(gate || state.pending || state.busy || state.storageBlocked);
  const differs = task && (!brief.document || brief.document.id !== task.source.brief_id || brief.document.revision !== task.source.brief_revision || brief.dirty);
  const pending = state.pending;
  return <section className="creator-panel creator-planner" aria-labelledby="planner-heading" aria-busy={state.busy || state.loading}>
    <header className="creator-section-head"><div><h2 id="planner-heading"><ClipboardList size={18} />准备规划任务</h2><p className="creator-muted">固定一个已保存版本，留下可恢复的规划记录。</p></div><span className="creator-badge"><ShieldCheck size={13} />创建不调用模型</span></header>
    <div className="creator-planner-intake">
      <div><p>{brief.document ? `准备冻结：${brief.document.title} · 简报 v${brief.document.revision}` : '还没有可冻结的保存版本'}</p><p id="planner-gate" className="creator-muted">{gate || '创建后停在待授权。不会自动制作视频，也不会把预算当作执行许可。'}</p></div>
      <button type="button" className="creator-primary" disabled={cannotCreate} aria-describedby="planner-gate" onClick={() => void controller.create(briefController.getSnapshot)}>准备规划任务</button>
    </div>
    <details className="creator-planner-offline"><summary>高级 · 离线控制流验证（测试响应）</summary><p className="creator-muted">使用当前已保存版本冻结来源，只有模型响应是合成的。不会调用真实模型或生成媒体；测试计划不代表 AI 已创作或内容质量通过。创建后还需明确启动。</p><div className="creator-action-row"><label className="creator-label" htmlFor="planner-scenario">测试情形</label><select id="planner-scenario" className="creator-field" value={scenario} onChange={event => setScenario(event.target.value as PlannerScenario)}>{scenarios.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select><button type="button" className="creator-secondary" disabled={cannotCreate} aria-describedby="planner-gate" onClick={() => void controller.create(briefController.getSnapshot, 'offline_test', scenario)}>创建离线验证任务</button></div></details>
    <div className="creator-planner-feedback">
      {state.busy && <p role="status" className="creator-muted">正在确认规划请求，原请求键已保留…</p>}
      {state.notice && <p role="status" className="creator-notice">{state.notice}</p>}
      {state.error && <div role="alert" className="creator-notice creator-notice-error"><p>{state.error}</p><button type="button" className="creator-secondary" disabled={state.busy || state.loading} onClick={() => { if (state.pending || state.storageBlocked) void controller.recover(false); else if (task) void controller.refresh(); else void controller.loadList(); }}>重新查询</button></div>}
      {pending && <section className="creator-notice" aria-label="原规划请求待确认"><h3>原规划请求待确认</h3><p>{pending.kind === 'create' ? `原创建引用简报 v${pending.request.brief_revision}，不随当前编辑变化。` : `原动作：${pending.request.action === 'stop' ? '暂停' : '续行'}，基于任务 v${pending.request.expected_revision}；查询状态不能证明动作键已确认。`} 请先查询，确认 / 重试始终复用原键和原请求。</p><details><summary>查看原请求引用</summary><dl className="creator-record"><div><dt>原键</dt><dd><code>{pending.kind === 'create' ? pending.request.client_request_key : pending.request.client_action_key}</code></dd></div><div><dt>{pending.kind === 'create' ? '简报 ID' : '任务 ID'}</dt><dd><code>{pending.kind === 'create' ? pending.request.brief_id : pending.task_id}</code></dd></div></dl></details><div className="creator-action-row"><button type="button" className="creator-secondary" disabled={state.busy} onClick={() => void controller.recover(false)}>查询原规划请求</button><button type="button" className="creator-secondary" disabled={state.busy} onClick={() => void controller.recover(true)}>按原键确认 / 重试</button></div></section>}
    </div>
    <div className="creator-planner-grid">
      <nav className="creator-planner-list" aria-label="规划任务列表"><div className="creator-list-heading"><span>规划记录</span><button type="button" aria-label="刷新规划任务列表" disabled={state.listLoading} onClick={() => void controller.loadList()}><RefreshCw size={14} /></button></div>
        {state.listLoading && <p className="creator-muted" role="status">正在读取任务摘要…</p>}
        {!state.listLoading && !state.tasks.length && <p className="creator-muted">{state.error ? '记录暂不可用，请重新查询。' : '还没有规划任务。保存简报后可创建。'}</p>}
        {state.tasks.map(item => <button type="button" key={item.id} className="creator-brief-item" aria-current={task?.id === item.id ? 'true' : undefined} onClick={() => void controller.open(item.id)}><span><strong>{item.source.title}</strong><small>简报 v{item.source.brief_revision} · {item.mode === 'offline_test' ? '离线测试响应' : '冻结来源记录'}</small><small>{item.mode === 'offline_test' ? statusLabel[item.status] : '授权、进度与计划请查看详情'}</small><small><time dateTime={item.updated_at}>{time(item.updated_at)}</time></small></span></button>)}
        {state.nextOffset !== null && <button type="button" className="creator-text-button" disabled={state.listLoading} onClick={() => void controller.loadList(true)}>加载更多任务</button>}
      </nav>
      <div className="creator-planner-detail" aria-busy={state.loading || state.refreshing}>
        {state.loading && <p className="creator-muted" role="status">正在读取规划任务…</p>}
        {!state.loading && !task && <p className="creator-muted">选择一条规划记录，查看它的冻结版本、状态与计划。</p>}
        {task && <>
          <header className="creator-planner-task-head"><div><h3>{task.mode === 'offline_test' ? '离线验证 · 测试响应' : '冻结来源记录'}</h3><p className="creator-badge" role="status">{task.mode === 'offline_test' ? statusLabel[task.status] : `已固定简报 v${task.source.brief_revision} · 授权与费用见下方`}</p></div><button type="button" className="creator-secondary" disabled={state.refreshing || state.busy} onClick={() => void controller.refresh()}><RefreshCw size={14} />刷新{task.mode === 'normal' ? '来源记录' : '任务'}</button></header>
          {task.mode === 'offline_test' && <p className="creator-notice">此任务使用合成模型响应。以下计划与调用步数用于验证实际控制流，不是 AI 真实创作结果。情形：{scenarios.find(item => item.value === task.scenario)?.label}</p>}
          {task.mode === 'offline_test' && task.blocked_reason && <p className="creator-notice">{reasonLabel[task.blocked_reason] || '任务已阻断，请核对服务器记录。'}</p>}
          <dl className="creator-record creator-planner-record"><div><dt>冻结来源</dt><dd>{task.source.title} · 简报 v{task.source.brief_revision}</dd></div><div><dt>来源版本 / 冻结时间</dt><dd>v{task.source.source_version} · <time dateTime={task.source.frozen_at}>{time(task.source.frozen_at)}</time></dd></div><div><dt>规格 / 预算草稿</dt><dd>写实影视 · 9:16 · 15秒 / {task.source.budget_cny === null ? '待定' : `¥${task.source.budget_cny}`}</dd></div><div><dt>{task.mode === 'normal' ? '来源记录调用 / 修正' : '调用 / 修正次数'}</dt><dd>{task.step_count} / {task.limits.max_steps} 步 · {task.repair_count} / {task.limits.max_repairs} 次修正</dd></div><div><dt>内容质量</dt><dd>未审核 · 不代表已接受</dd></div></dl>
          {differs && <p className="creator-planner-difference">当前编辑与此冻结版本不同。此任务仍使用上面的原版本，当前修改不会更新旧任务。</p>}
          {task.mode === 'offline_test' && <div className="creator-action-row">{task.allowed_actions.map(action => <button type="button" key={action} className="creator-secondary" disabled={state.busy || Boolean(pending) || state.storageBlocked || state.refreshing} onClick={() => void controller.action(action)}>{action === 'stop' ? '暂停任务' : task.collaboration ? '启动 / 续行规划与独立审阅验证' : '启动 / 续行离线验证'}</button>)}</div>}
          {task.pending_call && <p className="creator-muted">{task.pending_call.status === 'intent' ? '调用意图已保存；回执尚未确认，不能自动重调。' : '已收到持久回执，恢复时复用已确认结果。'}</p>}
          <details className="creator-conflict-details"><summary>查看冻结来源与核验引用</summary><p>任务：<code>{task.id}</code> · 状态版本 v{task.revision}<br />快照摘要：<code>{task.source.snapshot_hash}</code></p><button type="button" className="creator-secondary" disabled={state.sourceLoading} onClick={() => void controller.loadSource()}>{state.sourceLoading ? '正在读取冻结正文…' : '按任务 ID 读取冻结正文'}</button>{state.source && <div><label className="creator-label" htmlFor="planner-frozen-script">任务冻结剧本（只读）</label><textarea id="planner-frozen-script" className="creator-field" readOnly rows={7} value={state.source.script} /><h4>冻结制作要求</h4><p className="creator-planner-preserve">{state.source.requirements || '未填写'}</p></div>}</details>
          {task.mode === 'normal' ? <PlannerExecutionPanel task={task} /> : <section className="creator-planner-plan" aria-label="结构化镜头计划"><h3>测试镜头计划</h3>{!task.plan ? <p className="creator-muted">暂无结构合格的镜头计划。</p> : <><p className="creator-muted">计划 v{task.plan_version} · 15秒 · 9:16。结构校验合格，剧本语义与成片质量均未验收。</p><ol className="creator-planner-shots">{task.plan.shots.map(shot => <li key={shot.id}><header><strong>{shot.id}</strong><span>{shot.duration_ms / 1000} 秒</span></header><dl><dt>画面</dt><dd>{shot.visual}</dd><dt>运镜</dt><dd>{shot.camera || '未指定'}</dd><dt>声音</dt><dd>{shot.audio || '未指定'}</dd><dt>依赖</dt><dd>{shot.depends_on.join('、') || '无'}</dd></dl></li>)}</ol></>}</section>}
          {task.collaboration && <PlannerCollaborationPanel task={task} />}
          {task.mode === 'normal' && <details className="creator-conflict-details"><summary>高级 · 原来源记录控制（不执行模型）</summary><p>D08来源记录：{statusLabel[task.status]}。这里的动作不启动D09执行，也不改变冻结简报；更改来源记录版本后需重新核对报价引用。</p><div className="creator-action-row">{task.allowed_actions.map(action => <button type="button" key={action} className="creator-secondary" disabled={state.busy || Boolean(pending) || state.storageBlocked || state.refreshing} onClick={() => void controller.action(action)}>{action === 'stop' ? '暂停原来源记录' : '原来源记录恢复至待授权'}</button>)}</div></details>}
          <details className="creator-conflict-details"><summary>{task.mode === 'normal' ? '来源安全记录' : '安全事件记录'}（{state.events.length}）</summary><p>来源核验（D08）：仅含事件类型与安全代码，不含剧本、模型原文或工具参数。</p><ol className="creator-planner-events">{state.events.map(event => <li key={event.sequence}><span>#{event.sequence} · 任务 v{event.task_revision}{event.step !== null ? ` · 步骤 ${event.step}` : ''}</span><code>{event.type}{event.code ? ` / ${event.code}` : ''}</code><time dateTime={event.created_at}>{time(event.created_at)}</time></li>)}</ol>{state.moreEvents && <button type="button" className="creator-secondary" onClick={() => void controller.loadEvents()}>读取后续事件</button>}</details>
        </>}
      </div>
    </div>
  </section>;
}
