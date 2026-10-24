import type { PlannerDetail, PlannerReviewRuleId, PlannerReviewTarget } from '@/lib/briefPlannerTypes';

const criteria: Array<{ id: PlannerReviewRuleId; name: string }> = [
  { id: 'RV-F', name: '剧本忠实' }, { id: 'RV-T', name: '15秒可拍性' }, { id: 'RV-C', name: '人物与空间连续性' },
  { id: 'RV-A', name: '镜头动作与运镜' }, { id: 'RV-G', name: '提示可生成性' },
];
const verdicts = { accepted: '测试结论接受', rejected: '测试结论拒绝', unknown: '测试结论缺证' };
const statuses = {
  awaiting_plan: '等待 Planner 提交计划', review_ready: '计划已接收 · 等待交给 Reviewer', review_running: 'Reviewer 独立首审中',
  accepted: '合成审阅完成 · 五项测试结论接受', rejected: '合成审阅拒绝 · 已准备有限返工提案', unknown: '合成审阅缺证 · 需要补充信息',
  response_unknown: 'Reviewer 响应待确认', invalid: '审阅格式或绑定无效 · 无有效结论',
};
const locations = (items: PlannerReviewTarget[]) => items.map(t => t.shot_id === '*' ? '整份计划' : `${t.shot_id}.${t.field}`).join('、') || '无';

export default function PlannerCollaborationPanel({ task }: { task: PlannerDetail }) {
  const collaboration = task.collaboration;
  if (!collaboration) return null;
  const { binding, report, repair_task: repair } = collaboration;
  return <section className="creator-execution" aria-labelledby="collaboration-heading">
    <header><h3 id="collaboration-heading">规划 → 独立审阅 · 合成协作验证</h3><p className="creator-muted">一次启动后自动交接。Reviewer 使用重新建立的完整来源、确切计划与规则，不继承 Planner 的历史、推理或自评。</p></header>
    <p className="creator-notice">这里使用合成响应验证角色交接与恢复。审阅结论不代表真实作品质量通过；实际改稿、复核、素材、视频和人工验收均未运行。</p>
    <p className="creator-badge" role="status">{statuses[collaboration.status]}</p>
    <dl className="creator-record creator-planner-record">
      <div><dt>Planner · 规划角色</dt><dd>{collaboration.planner_call_count} 次调用意图{task.plan ? ` · 已提交计划 v${task.plan_version}` : ' · 尚无已接收计划'}</dd></div>
      <div><dt>Reviewer · 独立首审角色</dt><dd>{collaboration.reviewer_call_count} / {collaboration.max_reviewer_calls} 次调用意图</dd></div>
      <div><dt>两个角色累计上限</dt><dd>{task.step_count} / {collaboration.max_total_calls} 次 · 暂停与恢复不重置</dd></div>
      <div><dt>真实付费调用额度</dt><dd>{collaboration.paid_call_limit} 次</dd></div>
      <div><dt>当前首审绑定</dt><dd>{binding ? `计划 v${binding.plan_version} · ${binding.rule_version}` : '尚未创建 Reviewer 调用意图'}</dd></div>
      <div><dt>真实内容质量 / 人工接受</dt><dd>未审核 / 待验收</dd></div>
    </dl>
    {task.status === 'stopped' && <p className="creator-notice">任务已暂停，阻止后续角色委派；在途返回仍会保存原回执。只有明确续行后才继续处理，不会清零调用数。</p>}
    {collaboration.status === 'response_unknown' && <p className="creator-notice">Reviewer 已有调用意图，但没有确认回执。保留原未决操作，不能自动重调；查询和暂停不会把未知结果改成已完成。</p>}
    {collaboration.status === 'invalid' && <p className="creator-notice">没有可接受的结构化报告。格式或绑定错误不等于计划内容被拒绝，也不能显示为内容合格。</p>}
    <section className="creator-execution-block" aria-labelledby="review-criteria-heading"><h4 id="review-criteria-heading">五项首审标准</h4>
      <ol className="creator-planner-shots">{criteria.map(criterion => {
        const rule = report?.rules.find(item => item.rule_id === criterion.id);
        return <li key={criterion.id}><header><strong>{criterion.id} · {criterion.name}</strong><span>{rule ? verdicts[rule.verdict] : '尚无有效结论'}</span></header>
          {rule && <><p>{rule.checked_scope}</p><details className="creator-conflict-details"><summary>查看定位依据与问题（{rule.findings.length}）</summary>
            {rule.evidence.map((evidence, index) => <p key={index}>{evidence.source_segment_ids.join('、')} · {locations(evidence.targets)}<br />{evidence.note}</p>)}
            {rule.findings.map(finding => <section className="creator-execution-block" key={finding.finding_id}><h4>问题 {finding.finding_id}</h4><dl className="creator-record creator-planner-record">
              <div><dt>镜头定位 / 来源段</dt><dd>{locations(finding.targets)} / {finding.source_segment_ids.join('、')}</dd></div>
              <div><dt>问题</dt><dd>{finding.problem}</dd></div><div><dt>影响</dt><dd>{finding.impact}</dd></div>
              <div><dt>最小修改范围</dt><dd>{locations(finding.repair_scope)}</dd></div><div><dt>保留范围</dt><dd>{locations(finding.preserve)}</dd></div>
              <div><dt>后续复核条件</dt><dd>{finding.recheck_condition}</dd></div>
            </dl></section>)}
            {!rule.findings.length && <p className="creator-muted">本项没有提出定位问题。</p>}
          </details></>}
        </li>;
      })}</ol>
    </section>
    <section className="creator-execution-block" aria-labelledby="review-repair-heading"><h4 id="review-repair-heading">有限返工提案</h4>
      {repair ? <><p className="creator-muted">{repair.kind === 'needs_input' ? '需要补充信息，当前不能进入内容返工。' : '已准备最小内容返工范围，尚未改稿。'} 目标版本 v{repair.binding.next_plan_version} 尚未创建；旧版结论不能自动用于新版。</p>
        <dl className="creator-record creator-planner-record"><div><dt>问题引用</dt><dd>{repair.finding_refs.map(f => `${f.rule_id} / ${f.finding_id}`).join('、')}</dd></div>
          <div><dt>可修改字段</dt><dd>{locations(repair.repair_targets)}</dd></div><div><dt>需重新核对的镜头</dt><dd>{repair.affected_shot_ids.join('、') || '无'}</dd></div>
        </dl><details className="creator-conflict-details"><summary>查看必须保留的字段与不变量</summary><p>{locations(repair.preserve_targets)}</p><p className="creator-muted">镜头成员与顺序、ID、来源、规则、15秒总时长和9:16画幅保持不变。依赖后继只进入复核范围，不自动获得编辑权。</p></details>
      </> : <p className="creator-muted">{report?.overall_verdict === 'accepted' ? '本次合成首审未提出返工，不为演示制造缺陷。' : '暂无有效返工提案。'} 实际改稿与独立复核仍未运行。</p>}
    </section>
    {binding && <details className="creator-conflict-details"><summary>核验当前计划、首审与报告引用</summary><dl className="creator-record creator-planner-record">
      <div><dt>计划版本 / 摘要</dt><dd>v{binding.plan_version} / <code>{binding.plan_hash}</code></dd></div><div><dt>来源快照</dt><dd><code>{binding.source_snapshot_hash}</code></dd></div>
      <div><dt>审阅 / 操作</dt><dd><code>{binding.review_id} / {binding.intent_id}</code></dd></div><div><dt>规则版本 / 摘要</dt><dd>{binding.rule_version} / <code>{binding.rule_hash}</code></dd></div>
      <div><dt>报告摘要</dt><dd><code>{collaboration.report_hash || '尚无有效报告'}</code></dd></div>
    </dl><p className="creator-muted">前端摘要核对不能替代服务端的来源、版本、权限和报告校验。</p></details>}
  </section>;
}
