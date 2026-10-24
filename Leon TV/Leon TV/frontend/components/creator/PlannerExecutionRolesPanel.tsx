import type { ExecutionDetail, ExecutionRoleState } from '@/lib/plannerExecutionTypes';

const statuses: Record<ExecutionRoleState['status'], string> = {
  awaiting_plan: '等待 Planner 提交计划', review_ready: 'v1 已接收 · 等待独立首审', review_running: 'Reviewer 正在首审 v1',
  review_response_unknown: '首审回执待确认', review_invalid: '首审无有效结论', needs_input: '需要补充信息 · 未委派改稿',
  repair_ready: '首审拒绝 · 等待有限改稿', repair_running: 'Repairer 正在有限改稿', repair_response_unknown: '改稿回执待确认', repair_invalid: '改稿无效 · 保留 v1',
  recheck_ready: 'v2 已接收 · 等待独立复核', recheck_running: 'Rechecker 正在独立复核 v2', recheck_response_unknown: '复核回执待确认', recheck_invalid: '复核无有效结论',
  accepted: '当前版本文本审阅接受', rejected: 'v2 复核拒绝 · 改稿上限已用', unknown: 'v2 复核缺证 · 已停止',
};
const verdicts = { accepted: '接受', rejected: '拒绝', unknown: '缺证' };
const conclusion = (verdict: ExecutionRoleState['first_verdict']) => verdict ? verdicts[verdict] : '尚无有效结论';

export default function PlannerExecutionRolesPanel({ execution }: { execution: ExecutionDetail }) {
  const state = execution.role_state;
  if (!state) return null;
  const currentVersion = state.plans.at(-1)?.version;
  return <section className="creator-execution-block" aria-labelledby="execution-roles-heading">
    <h4 id="execution-roles-heading">文本角色协作结果（只读）</h4>
    <p className="creator-badge" role="status">{statuses[state.status]}{state.accepted_plan_version ? ` · v${state.accepted_plan_version}` : ''}</p>
    <p className="creator-muted">一次开始后自动交接 Planner、Reviewer、Repairer、Rechecker。四个角色共用原任务、授权、累计调用与费用账本，不分别获得新额度。</p>
    <dl className="creator-record"><div><dt>Planner · 规划调用</dt><dd>{state.planner_call_count} 次</dd></div>
      <div><dt>Reviewer · 首审 v1</dt><dd>{state.reviewer_call_count} / {state.max_reviewer_calls} 次 · {conclusion(state.first_verdict)}</dd></div>
      <div><dt>Repairer · 有限改稿</dt><dd>{state.repairer_call_count} / {state.max_repairer_calls} 次</dd></div>
      <div><dt>Rechecker · 独立复核 v2</dt><dd>{state.rechecker_call_count} / {state.max_rechecker_calls} 次 · {conclusion(state.recheck_verdict)}</dd></div>
      <div><dt>共享调用 / 角色硬上限</dt><dd>{execution.ledger.call_count} / {state.max_total_calls} 次 · 报价累计上限见共享账本</dd></div>
      <div><dt>当前已接收版本 / 接受绑定</dt><dd>{currentVersion ? `v${currentVersion}` : '尚无计划'} / {state.accepted_plan_version ? `仅 v${state.accepted_plan_version}` : '尚无当前版本接受'}</dd></div>
    </dl>
    {state.status.endsWith('_response_unknown') && <p className="creator-notice">当前角色回执未确认，原意图、调用数和未决预占保留，不能自动重调。核对已有回执不会发起新模型调用。</p>}
    {['rejected','unknown','recheck_invalid'].includes(state.status) && <p className="creator-notice">本轮有限改稿和复核已停止，不再自动创建下一版本；v1 首审结论不能用于 v2。</p>}
    {state.accepted_plan_version === 1 && <p className="creator-muted">首审已接受 v1，本轮未为演示制造改稿或 v2。</p>}
    <p className="creator-muted">这里只显示安全状态、结论与版本引用。{execution.environment === 'synthetic_test' ? '合成结论不构成真实文字质量验收。' : '结论仅绑定该计划版本的一次文字审阅。'}媒体生成与人工验收尚未完成，供应商账单未确认。</p>
    <details className="creator-conflict-details"><summary>查看首审、改稿、复核与版本绑定</summary><dl className="creator-record">
      <div><dt>首审版本 / 报告摘要</dt><dd>{state.first_review_binding ? `v${state.first_review_binding.plan_version}` : '未委派'} / <code>{state.first_report_hash ?? '尚无有效报告'}</code></dd></div>
      <div><dt>改稿操作 / 范围摘要</dt><dd><code>{state.repair_binding?.intent_id ?? '未委派'} / {state.repair_task_hash ?? '尚无提案'}</code></dd></div>
      <div><dt>复核版本 / 报告摘要</dt><dd>{state.recheck_binding ? `v${state.recheck_binding.plan_version}` : '未委派'} / <code>{state.recheck_report_hash ?? '尚无有效报告'}</code></dd></div>
      <div><dt>来源快照</dt><dd><code>{execution.source.snapshot_hash}</code></dd></div>
    </dl>{state.plans.map(plan => <p key={plan.version}>计划 v{plan.version}：<code>{plan.plan_hash}</code></p>)}
    <p className="creator-muted">服务端负责来源、回执、权限与内容摘要校验；当前面板不读取原始回复、推理、完整审阅上下文或私有路径。</p></details>
  </section>;
}
