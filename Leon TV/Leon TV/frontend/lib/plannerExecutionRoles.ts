import { PLANNER_REVIEW_RULE_HASH, pythonContractJson } from './briefPlannerApi';
import type { PlannerRepairBinding, PlannerReviewBinding, PlannerReviewVerdict } from './briefPlannerTypes';
import { EXECUTION_ROLE_SCOPE, EXECUTION_ROLE_WORKFLOW, type ExecutionDetail, type ExecutionGrant, type ExecutionOperation, type ExecutionPolicy, type ExecutionQuote, type ExecutionRole, type ExecutionRoleState } from './plannerExecutionTypes';

/** Python creator.digest of the fixed public scope; this does not confirm billing. */
export const EXECUTION_ROLE_SCOPE_HASH = '84e61b299793a434788892ac663085e68d6df63cb2caa9652ac6a8626687e644';
const ROLES: ExecutionRole[] = ['planner','reviewer','repairer','rechecker'];
const STATUSES = ['awaiting_plan','review_ready','review_running','review_response_unknown','repair_ready','repair_running','repair_response_unknown','recheck_ready','recheck_running','recheck_response_unknown','accepted','rejected','unknown','needs_input','review_invalid','repair_invalid','recheck_invalid'];
const STATE_FIELDS = ['schema_version','status','roles','planner_call_count','reviewer_call_count','repairer_call_count','rechecker_call_count',
  'max_total_calls','max_reviewer_calls','max_repairer_calls','max_rechecker_calls','first_review_binding','first_report_hash','first_verdict',
  'repair_binding','repair_task_hash','recheck_binding','recheck_report_hash','recheck_verdict','accepted_plan_version','plans'];
const present = (value: Record<string, unknown>, key: string) => Object.prototype.hasOwnProperty.call(value, key);
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const identifier = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/.test(value);
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const verdict = (value: unknown): value is PlannerReviewVerdict | null => value === null || value === 'accepted' || value === 'rejected' || value === 'unknown';

/** Additive readers reject partial tags instead of silently downgrading authority. */
export class ExecutionRoleReader {
  constructor(private invalid: () => never) {}
  private object(value: unknown): Record<string, unknown> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return this.invalid();
    return value as Record<string, unknown>;
  }
  private exact(value: Record<string, unknown>, fields: string[]) { if (Object.keys(value).sort().join('|') !== [...fields].sort().join('|')) this.invalid(); }
  private workflow(value: Record<string, unknown>, fields: string[]): boolean {
    if (!['role_workflow', ...fields].some(field => present(value, field))) return false;
    if (value.role_workflow !== EXECUTION_ROLE_WORKFLOW || fields.some(field => !present(value, field))) return this.invalid();
    return true;
  }
  policy(value: Record<string, unknown>): Pick<ExecutionPolicy, 'role_workflow'> {
    return this.workflow(value, []) ? { role_workflow: EXECUTION_ROLE_WORKFLOW } : {};
  }
  quote(value: Record<string, unknown>, policy: ExecutionPolicy): Pick<ExecutionQuote, 'role_workflow' | 'role_scope'> {
    const tagged = this.workflow(value, ['role_scope']);
    if (tagged !== Boolean(policy.role_workflow)) return this.invalid();
    if (!tagged) return {};
    if (pythonContractJson(value.role_scope) !== pythonContractJson(EXECUTION_ROLE_SCOPE)) return this.invalid();
    return { role_workflow: EXECUTION_ROLE_WORKFLOW, role_scope: structuredClone(EXECUTION_ROLE_SCOPE) };
  }
  grant(value: Record<string, unknown>): Pick<ExecutionGrant, 'role_workflow' | 'role_scope_hash'> {
    if (!this.workflow(value, ['role_scope_hash'])) return {};
    if (value.role_scope_hash !== EXECUTION_ROLE_SCOPE_HASH) return this.invalid();
    return { role_workflow: EXECUTION_ROLE_WORKFLOW, role_scope_hash: EXECUTION_ROLE_SCOPE_HASH };
  }
  operation(value: Record<string, unknown>): Pick<ExecutionOperation, 'role_workflow' | 'role' | 'role_call_index' | 'role_binding_hash'> {
    if (!this.workflow(value, ['role','role_call_index','role_binding_hash'])) return {};
    if (!ROLES.includes(value.role as ExecutionRole) || !count(value.role_call_index) || value.role_call_index < 1 || value.role_call_index > 6 ||
      value.role !== 'planner' && value.role_call_index !== 1 || !hash(value.role_binding_hash)) return this.invalid();
    return { role_workflow: EXECUTION_ROLE_WORKFLOW, role: value.role as ExecutionRole, role_call_index: value.role_call_index, role_binding_hash: value.role_binding_hash };
  }
  private binding(value: unknown, execution: ExecutionDetail, plans: ExecutionRoleState['plans'], version: 1 | 2): PlannerReviewBinding {
    const b = this.object(value);
    this.exact(b, ['review_id','run_id','intent_id','task_id','plan_version','plan_hash','source_snapshot_hash','rule_version','rule_hash']);
    if (!identifier(b.review_id) || !identifier(b.intent_id) || b.run_id !== execution.task_id || b.task_id !== execution.task_id || b.plan_version !== version ||
      !hash(b.plan_hash) || b.plan_hash !== plans[version - 1]?.plan_hash || b.source_snapshot_hash !== execution.source.snapshot_hash ||
      b.rule_version !== 'D15-RV-v0.2' || b.rule_hash !== PLANNER_REVIEW_RULE_HASH) return this.invalid();
    return { review_id: b.review_id, run_id: execution.task_id, intent_id: b.intent_id, task_id: execution.task_id, plan_version: version,
      plan_hash: b.plan_hash, source_snapshot_hash: execution.source.snapshot_hash, rule_version: 'D15-RV-v0.2', rule_hash: PLANNER_REVIEW_RULE_HASH };
  }
  detail(value: Record<string, unknown>, execution: ExecutionDetail): Pick<ExecutionDetail, 'role_workflow' | 'role_state'> {
    const tagged = this.workflow(value, ['role_state']);
    const nested = [...execution.quotes, ...execution.grants, ...execution.operations];
    if (nested.some(item => Boolean(item.role_workflow) !== tagged)) return this.invalid();
    if (!tagged) return {};
    if (execution.environment === 'disabled') return this.invalid();
    const s = this.object(value.role_state); this.exact(s, STATE_FIELDS);
    if (s.schema_version !== 1 || typeof s.status !== 'string' || !STATUSES.includes(s.status) || pythonContractJson(s.roles) !== pythonContractJson(ROLES) ||
      s.max_total_calls !== 6 || s.max_reviewer_calls !== 1 || s.max_repairer_calls !== 1 || s.max_rechecker_calls !== 1 ||
      !hashOrNull(s.first_report_hash) || !hashOrNull(s.repair_task_hash) || !hashOrNull(s.recheck_report_hash) || !verdict(s.first_verdict) || !verdict(s.recheck_verdict) ||
      !Array.isArray(s.plans) || s.plans.length > 2) return this.invalid();
    const plans = s.plans.map((raw, index) => {
      const p = this.object(raw); this.exact(p, ['version','plan_hash']);
      if (p.version !== index + 1 || !hash(p.plan_hash)) return this.invalid();
      return { version: p.version as 1 | 2, plan_hash: p.plan_hash };
    });
    if (Boolean(execution.plan) !== Boolean(plans.length)) return this.invalid();
    const counts = { planner: 0, reviewer: 0, repairer: 0, rechecker: 0 };
    let previousRole = 0;
    for (const operation of execution.operations) {
      const role = operation.role!, order = ROLES.indexOf(role);
      if (order < previousRole || order > previousRole + 1 || order > 0 && counts[ROLES[order - 1]] === 0) return this.invalid();
      previousRole = order; counts[role]++;
      if (operation.role_call_index !== counts[role] || role !== 'planner' && counts[role] > 1) return this.invalid();
    }
    for (const role of ROLES) if (!count(s[`${role}_call_count`]) || s[`${role}_call_count`] !== counts[role]) return this.invalid();
    if (execution.ledger.call_count !== execution.operations.length || execution.ledger.call_count > 6) return this.invalid();
    const first = s.first_review_binding === null ? null : this.binding(s.first_review_binding, execution, plans, 1);
    const recheck = s.recheck_binding === null ? null : this.binding(s.recheck_binding, execution, plans, 2);
    if (Boolean(first) !== Boolean(counts.reviewer) || Boolean(recheck) !== Boolean(counts.rechecker) ||
      (s.first_report_hash === null) !== (s.first_verdict === null) || (s.recheck_report_hash === null) !== (s.recheck_verdict === null) ||
      s.first_verdict !== null && !first || s.recheck_verdict !== null && !recheck) return this.invalid();
    let repair: PlannerRepairBinding | null = null;
    if (s.repair_binding !== null) {
      const b = this.object(s.repair_binding);
      this.exact(b, ['task_id','run_id','intent_id','source_snapshot_hash','parent_plan_version','parent_plan_hash','review_id','report_hash','repair_task_hash','next_plan_version']);
      if (!first || !identifier(b.intent_id) || b.task_id !== execution.task_id || b.run_id !== execution.task_id || b.source_snapshot_hash !== execution.source.snapshot_hash ||
        b.parent_plan_version !== 1 || b.parent_plan_hash !== first.plan_hash || b.review_id !== first.review_id || b.report_hash !== s.first_report_hash ||
        !hash(b.repair_task_hash) || b.repair_task_hash !== s.repair_task_hash || b.next_plan_version !== 2) return this.invalid();
      repair = { task_id: execution.task_id, run_id: execution.task_id, intent_id: b.intent_id, source_snapshot_hash: execution.source.snapshot_hash,
        parent_plan_version: 1, parent_plan_hash: first.plan_hash, review_id: first.review_id, report_hash: s.first_report_hash as string,
        repair_task_hash: b.repair_task_hash, next_plan_version: 2 };
    }
    if (Boolean(repair) !== Boolean(counts.repairer) || repair && s.first_verdict !== 'rejected' || recheck && (!repair || plans.length !== 2 ||
      recheck.review_id === first?.review_id || recheck.intent_id === first?.intent_id || recheck.intent_id === repair.intent_id)) return this.invalid();
    const bindings = { reviewer: first, repairer: repair, rechecker: recheck };
    for (const [role, binding] of Object.entries(bindings)) if (binding) {
      const operation = execution.operations.find(op => op.role === role);
      if (!operation || operation.id !== binding.intent_id) return this.invalid();
    }
    if (repair && repair.intent_id === first?.intent_id || s.first_verdict === 'accepted' && (repair || recheck || plans.length !== 1) ||
      s.first_verdict === 'unknown' && (repair || recheck) || plans.length === 2 && !repair) return this.invalid();
    if (s.status === 'awaiting_plan' && (plans.length || first) || s.status === 'review_ready' && (plans.length !== 1 || first) ||
      s.status === 'repair_ready' && (s.first_verdict !== 'rejected' || !s.repair_task_hash || repair) ||
      s.status === 'recheck_ready' && (!repair || plans.length !== 2 || recheck) ||
      s.first_verdict === 'accepted' && s.status !== 'accepted' || s.recheck_verdict !== null && s.status !== s.recheck_verdict ||
      s.repair_task_hash !== null && (s.first_verdict === null || s.first_verdict === 'accepted')) return this.invalid();
    if (s.status === 'accepted') {
      if (s.accepted_plan_version !== plans.length || plans.length === 1 && s.first_verdict !== 'accepted' || plans.length === 2 && s.recheck_verdict !== 'accepted' || !plans.length || execution.status !== 'plan_ready') return this.invalid();
    } else if (s.accepted_plan_version !== null || execution.status === 'plan_ready') return this.invalid();
    if (s.status === 'rejected' && s.recheck_verdict !== 'rejected' || s.status === 'unknown' && s.recheck_verdict !== 'unknown' ||
      s.status === 'needs_input' && (!first || s.first_verdict === null || s.first_verdict === 'accepted' || repair) ||
      ['review_running','review_response_unknown','review_invalid'].includes(s.status) && (!first || s.first_verdict !== null) ||
      ['repair_running','repair_response_unknown','repair_invalid'].includes(s.status) && (!repair || plans.length !== 1) ||
      ['recheck_running','recheck_response_unknown','recheck_invalid'].includes(s.status) && (!recheck || s.recheck_verdict !== null)) return this.invalid();
    return { role_workflow: EXECUTION_ROLE_WORKFLOW, role_state: { schema_version: 1, status: s.status as ExecutionRoleState['status'], roles: ['planner','reviewer','repairer','rechecker'],
      planner_call_count: counts.planner, reviewer_call_count: counts.reviewer, repairer_call_count: counts.repairer, rechecker_call_count: counts.rechecker,
      max_total_calls: 6, max_reviewer_calls: 1, max_repairer_calls: 1, max_rechecker_calls: 1, first_review_binding: first,
      first_report_hash: s.first_report_hash, first_verdict: s.first_verdict, repair_binding: repair, repair_task_hash: s.repair_task_hash,
      recheck_binding: recheck, recheck_report_hash: s.recheck_report_hash, recheck_verdict: s.recheck_verdict,
      accepted_plan_version: s.accepted_plan_version as 1 | 2 | null, plans } };
  }
}
function hashOrNull(value: unknown): value is string | null { return value === null || hash(value); }
