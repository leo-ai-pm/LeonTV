import type { ExecutionDetail } from '@/lib/plannerExecutionTypes';
import { EMPIRICAL_RISK_COPY } from '@/lib/plannerExecutionRiskView';

const amount = (value: string | null | undefined) => value == null ? '未确认' : `¥${value}`;

export default function PlannerExecutionRiskPanel({ execution, pending }: { execution: ExecutionDetail; pending: boolean }) {
  const ledger = execution.ledger;
  const unobserved = execution.operations.filter(op => op.platform_observed_cny == null).length;
  const unestimated = execution.operations.filter(op => op.local_estimate_cny == null).length;
  return <section className="creator-execution-block" aria-label="经验风险与费用观察">
    <h4>经验风险与费用观察</h4>
    <p className="creator-muted">{EMPIRICAL_RISK_COPY.disclosure} {EMPIRICAL_RISK_COPY.retention}</p>
    {pending && <p className="creator-notice">原请求尚未确认，下面保留上次观察值。风险和费用不能按零或已释放处理。</p>}
    <dl className="creator-record creator-execution-ledger">
      <div><dt>任务已提交风险预留（持续保留）</dt><dd>{amount(ledger.risk_allocated_cny)}</dd></div>
      <div><dt>任务风险限额 / 已提交调用</dt><dd>{amount(ledger.task_limit_cny ?? undefined)} / {ledger.call_count} 次（上限 {ledger.max_calls ?? '待策略确定'}）</dd></div>
      <div><dt>任务本地估价合计（非平台扣费）</dt><dd>{amount(ledger.local_estimate_cny)}{unestimated > 0 ? `；${unestimated} 次尚无本地估价` : ''}</dd></div>
      <div><dt>任务平台已观察费用合计</dt><dd>{amount(ledger.platform_observed_cny)}{unobserved > 0 ? `；${unobserved} 次待核费，总额尚未确认` : ''}</dd></div>
      <div><dt>账户冻结起点风险 / 调用数</dt><dd>{amount(ledger.opening_exposure_cny)} / {ledger.opening_call_count ?? '未确认'} 次</dd></div>
      <div><dt>账户风险累计（含冻结起点）</dt><dd>{amount(ledger.account_risk_allocated_cny)}</dd></div>
      <div><dt>账户风险限额 / 累计调用数</dt><dd>{amount(ledger.account_limit_cny ?? undefined)} / {ledger.account_call_count} 次（上限 {ledger.account_max_calls ?? '待策略确定'}）</dd></div>
      <div><dt>当前账本新增调用的本地估价 / 平台观察</dt><dd>{amount(ledger.account_local_estimate_cny)} / {amount(ledger.account_platform_observed_cny)}</dd></div>
      <div><dt>供应商账单确认</dt><dd>未确认；风险预留、本地估价和平台观察均不是供应商最终账单</dd></div>
    </dl>
    <p className="creator-muted">冻结起点保留已有风险与调用数，不推定历史实际费用。以上三种金额分别记录，不相加成已花费金额；换授权或暂停不会重置风险和次数。</p>
    <details className="creator-conflict-details"><summary>核验冻结预算锚点</summary><p><code>{execution.parent_anchor_hash}</code></p><p>前端仅核对安全元数据的一致性；服务端负责预算来源、授权、回执与平台费用证据校验。</p></details>
  </section>;
}
