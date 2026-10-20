import type { ReactNode } from 'react';

/** Layout only. Request fields and available controls come from the D09 contract. */
export default function PlannerExecutionSection({ children, busy = false }: { children: ReactNode; busy?: boolean }) {
  return <section className="creator-execution" aria-labelledby="planner-execution-heading" aria-busy={busy}>
    <header><h3 id="planner-execution-heading">规划授权与费用</h3><p className="creator-muted">先核对报价与外发范围，再明确授权。授权后仍需单独开始规划。</p></header>
    {children}
    <p className="creator-muted creator-execution-footnote">费用预占会保留未决金额；预占与本地计价均不等于供应商账单。</p>
  </section>;
}
