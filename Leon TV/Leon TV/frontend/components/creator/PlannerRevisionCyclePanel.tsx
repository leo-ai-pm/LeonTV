import type { PlannerDetail, PlannerReviewRule, PlannerRecheckRule, PlannerReviewTarget, PlannerRevisionCycleSummary } from '@/lib/briefPlannerTypes';
import { plannerRevisionChanges } from '@/lib/plannerRevisionCycle';

const criteria = [{ id: 'RV-F', name: '剧本忠实' }, { id: 'RV-T', name: '15秒可拍性' }, { id: 'RV-C', name: '人物与空间连续性' },
  { id: 'RV-A', name: '镜头动作与运镜' }, { id: 'RV-G', name: '提示可生成性' }];
const verdicts = { accepted: '测试结论接受', rejected: '测试结论拒绝', unknown: '测试结论缺证' };
const resolutionLabels = { resolved: '合成复核：已解决', unresolved: '合成复核：仍未解决', unknown: '合成复核：缺证' };
const statusLabels: Record<PlannerRevisionCycleSummary['status'], string> = {
  awaiting_plan: '等待 Planner 提交 v1', review_ready: 'v1 已接收 · 等待首审', review_running: 'Reviewer 独立首审 v1',
  review_response_unknown: '首审响应待确认', review_invalid: '首审格式或绑定无效', needs_input: '首审缺证或没有可改范围 · 已停止',
  repair_ready: '首审已拒绝 · 等待有限改稿', repair_running: 'Repairer 正在限定范围内改稿', repair_invalid: '改稿无效 · 未保存 v2',
  repair_response_unknown: '改稿响应待确认 · 未保存 v2', recheck_ready: 'v2 已保存 · 等待独立复核', recheck_running: 'Rechecker 独立复核 v2',
  recheck_response_unknown: 'v2 复核响应待确认', recheck_invalid: 'v2 复核格式或绑定无效', accepted: '当前版本获得合成结论接受',
  rejected: 'v2 复核拒绝 · 改稿上限已用', unknown: 'v2 复核缺证 · 已停止',
};
const locations = (items: PlannerReviewTarget[]) => items.map(t => t.shot_id === '*' ? '整份计划' : `${t.shot_id}.${t.field}`).join('、') || '无';
const valueText = (value: string | number | string[]) => Array.isArray(value) ? value.join('、') || '无' : String(value) || '未指定';

function ReviewCriteria({ rules, version }: { rules: Array<PlannerReviewRule | PlannerRecheckRule> | undefined; version: 1 | 2 }) {
  return <ol className="creator-planner-shots">{criteria.map(criterion => {
    const rule = rules?.find(rule => rule.rule_id === criterion.id);
    return <li key={criterion.id}><header><strong>{criterion.id} · {criterion.name}</strong><span>{rule ? verdicts[rule.verdict] : `尚无 v${version} 有效结论`}</span></header>
      {rule && <><p>{rule.checked_scope}</p><details className="creator-conflict-details"><summary>查看依据与定位问题（{rule.findings.length}）</summary>
        {rule.evidence.map((e,index) => <p key={index}>{e.source_segment_ids.join('、')} · {locations(e.targets)}<br />{e.note}</p>)}
        {rule.findings.map(finding => <section className="creator-execution-block" key={finding.finding_id}><h4>{finding.finding_id}{finding.prior_finding_id ? ` · 对应旧问题 ${finding.prior_finding_id}` : ''}</h4>
          <dl className="creator-record creator-planner-record"><div><dt>定位 / 来源段</dt><dd>{locations(finding.targets)} / {finding.source_segment_ids.join('、')}</dd></div>
            <div><dt>问题 / 影响</dt><dd>{finding.problem}<br />{finding.impact}</dd></div><div><dt>最小修改 / 保留</dt><dd>{locations(finding.repair_scope)} / {locations(finding.preserve)}</dd></div>
            <div><dt>复核条件</dt><dd>{finding.recheck_condition}</dd></div></dl>
        </section>)}
        {!rule.findings.length && <p className="creator-muted">本项没有提出定位问题。</p>}
      </details></>}
    </li>;
  })}</ol>;
}

export default function PlannerRevisionCyclePanel({ task }: { task: PlannerDetail }) {
  const cycle = task.revision_cycle;
  if (!cycle) return null;
  const { parent_plan: parent, first_report: first, repair_task: repair, recheck_report: recheck } = cycle;
  const findings = first?.rules.filter(rule => rule.verdict !== 'accepted').flatMap(rule => rule.findings) || [];
  const changes = parent && task.plan && task.plan_version === 2 ? plannerRevisionChanges(parent, task.plan) : [];
  return <section className="creator-execution" aria-labelledby="revision-cycle-heading">
    <header><h3 id="revision-cycle-heading">首审 v1 → 有限改稿 → 独立复核 v2</h3><p className="creator-muted">一次启动后自动交接；最多一次内容改稿和一次复核，缺证或失败后保留记录并停止。</p></header>
    <p className="creator-notice">这是合成响应控制流验证。测试计划的版本保存、字段差异和恢复由实际代码处理；合成接受不代表真实作品质量通过。真实模型、素材、视频和人工验收均未运行。</p>
    <p className="creator-badge" role="status">{statusLabels[cycle.status]}{cycle.accepted_plan_version ? ` · v${cycle.accepted_plan_version}` : ''}</p>
    <dl className="creator-record creator-planner-record">
      <div><dt>Planner · 读取与提交</dt><dd>{cycle.planner_call_count} 次调用意图</dd></div>
      <div><dt>Reviewer · 首审 v1</dt><dd>{cycle.reviewer_call_count} / {cycle.max_reviewer_calls} 次</dd></div>
      <div><dt>Repairer · 有限改稿</dt><dd>{cycle.repairer_call_count} / {cycle.max_repairer_calls} 次</dd></div>
      <div><dt>Rechecker · 独立复核 v2</dt><dd>{cycle.rechecker_call_count} / {cycle.max_rechecker_calls} 次</dd></div>
      <div><dt>四个角色累计 / 真实付费额度</dt><dd>{task.step_count} / {cycle.max_total_calls} 次 / {cycle.paid_call_limit} 次付费调用</dd></div>
      <div><dt>当前计划 / 真实内容质量</dt><dd>{task.plan_version ? `v${task.plan_version}` : '尚无计划'} / 未审核 · 人工接受待验收</dd></div>
    </dl>
    {task.status === 'stopped' && <p className="creator-notice">已暂停后续角色委派。在途回执仍会保存，明确续行后只处理原回执；次数与版本不重置。</p>}
    {cycle.status.endsWith('_response_unknown') && <p className="creator-notice">该角色已有调用意图但没有确认回执，不能自动重调。查询、暂停和恢复不会把未知结果改成成功或清零计数。</p>}
    {['rejected','unknown','recheck_invalid'].includes(cycle.status) && <p className="creator-notice">当前 v2 没有获得有效合成接受。有限改稿额度已用，不再自动创建 v3；v1 首审结论不能用于 v2。</p>}
    <section className="creator-execution-block" aria-labelledby="revision-first-review"><h4 id="revision-first-review">1. 首审 v1 · {first ? verdicts[first.overall_verdict] : '尚无有效报告'}</h4>
      <ReviewCriteria rules={first?.rules} version={1} />
      {first?.overall_verdict === 'accepted' && <p className="creator-muted">v1 首审已合成接受，不制造返工或 v2。</p>}
    </section>
    <section className="creator-execution-block" aria-labelledby="revision-repair"><h4 id="revision-repair">2. 有限改稿范围与 v2 实际差异</h4>
      {repair ? <><p className="creator-muted">{repair.kind === 'needs_input' ? '缺少必要信息或可修改范围，未委派改稿。' : '只允许下列字段发生实际变化。依赖后继只进入复核范围，不自动获得编辑权。'}</p>
        <dl className="creator-record creator-planner-record"><div><dt>允许修改</dt><dd>{locations(repair.repair_targets)}</dd></div><div><dt>需重新核对的镜头</dt><dd>{repair.affected_shot_ids.join('、') || '无'}</dd></div></dl>
        <details className="creator-conflict-details"><summary>查看保留范围与不变量</summary><p>{locations(repair.preserve_targets)}</p><p className="creator-muted">镜头成员、顺序、ID、来源、规则、15秒总时长和9:16画幅保持不变。</p></details>
      </> : <p className="creator-muted">暂无有效返工提案。</p>}
      {changes.length ? <><p className="creator-muted">测试计划 v2 已保存。以下直接比较已接收 v1 与当前 v2：</p><ol className="creator-planner-shots">{changes.map(change => <li key={`${change.shot_id}.${change.field}`}><header><strong>{change.shot_id}.{change.field}</strong><span>v1 → v2</span></header><dl>
        <dt>原值</dt><dd>{valueText(change.before)}</dd><dt>新值</dt><dd>{valueText(change.after)}</dd>
      </dl></li>)}</ol></> : <p className="creator-muted">尚未保存有效 v2，当前没有已接收的字段变更。</p>}
      {parent && <details className="creator-conflict-details"><summary>查看首审使用的父计划 v1</summary><ol className="creator-planner-shots">{parent.shots.map(shot => <li key={shot.id}><header><strong>{shot.id}</strong><span>{shot.duration_ms / 1000} 秒</span></header><dl><dt>画面</dt><dd>{shot.visual}</dd><dt>运镜</dt><dd>{shot.camera || '未指定'}</dd><dt>声音</dt><dd>{shot.audio || '未指定'}</dd><dt>依赖</dt><dd>{shot.depends_on.join('、') || '无'}</dd></dl></li>)}</ol></details>}
    </section>
    <section className="creator-execution-block" aria-labelledby="revision-recheck"><h4 id="revision-recheck">3. 独立复核 v2 · 逐条旧问题与全部五项规则</h4>
      <p className="creator-muted">复核使用完整来源、v2、原规则与 v1 问题重新建立上下文，不继承 Planner 或 Repairer 的聊天、推理和自评。</p>
      {!findings.length && <p className="creator-muted">暂无首审旧问题可供复核。</p>}
      <ol className="creator-planner-shots">{findings.map(finding => {
        const resolution = recheck?.resolutions.find(item => item.prior_finding_id === finding.finding_id);
        return <li key={finding.finding_id}><header><strong>{finding.rule_id} / {finding.finding_id}</strong><span>{resolution ? resolutionLabels[resolution.status] : '尚无有效解决结论'}</span></header><p>{finding.problem}</p>
          <p className="creator-muted">原定位：{locations(finding.targets)} · {finding.source_segment_ids.join('、')}</p>
          {resolution && <><p>{resolution.note}</p><p className="creator-muted">复核定位：{locations(resolution.targets)} · {resolution.source_segment_ids.join('、')}</p></>}
        </li>;
      })}</ol>
      <ReviewCriteria rules={recheck?.rules} version={2} />
    </section>
    <details className="creator-conflict-details"><summary>核验父版本、改稿操作与当前复核绑定</summary><dl className="creator-record creator-planner-record">
      <div><dt>父计划 v1 / 首审报告</dt><dd><code>{cycle.first_review_binding?.plan_hash || '尚无绑定'} / {cycle.first_report_hash || '尚无有效报告'}</code></dd></div>
      <div><dt>改稿操作 / 提案摘要</dt><dd><code>{cycle.repair_binding?.intent_id || '未委派'} / {cycle.repair_binding?.repair_task_hash || '尚无绑定'}</code></dd></div>
      <div><dt>当前 v2 / 复核报告</dt><dd><code>{cycle.recheck_binding?.plan_hash || '尚无复核绑定'} / {cycle.recheck_report_hash || '尚无有效报告'}</code></dd></div>
      <div><dt>来源快照</dt><dd><code>{task.source.snapshot_hash}</code></dd></div>
    </dl><p className="creator-muted">所有角色共享原任务累计次数。前端摘要与差异检查不能替代服务端来源、权限、回执和范围校验。</p></details>
  </section>;
}
