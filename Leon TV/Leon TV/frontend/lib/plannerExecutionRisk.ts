import { EMPIRICAL_ACCOUNTING_KIND, EMPIRICAL_EXECUTION_SCOPE, EXECUTION_ROLE_WORKFLOW, type EmpiricalExecutionPolicy, type ExecutionDetail, type ExecutionGrant, type ExecutionLedger, type ExecutionOperation, type ExecutionPolicy, type ExecutionQuote } from './plannerExecutionTypes';

const present = (value: Record<string, unknown>, key: string) => Object.prototype.hasOwnProperty.call(value, key);
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const money = (value: unknown): value is string => typeof value === 'string' && /^\d+\.\d{6}$/.test(value);
const nullableMoney = (value: unknown): value is string | null => value === null || money(value);
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const units = (value: string) => BigInt(value.replace('.', ''));
const sum = (values: Array<string | null | undefined>) => values.reduce<bigint>((total, value) => total + (value == null ? BigInt(0) : units(value)), BigInt(0));
const KIND = EMPIRICAL_ACCOUNTING_KIND;
const LEDGER_FIELDS = ['opening_exposure_cny','opening_call_count','risk_allocated_cny','local_estimate_cny','platform_observed_cny','account_risk_allocated_cny','account_local_estimate_cny','account_platform_observed_cny'];
const NULLABLE_TOTALS = ['local_estimate_cny','platform_observed_cny','account_local_estimate_cny','account_platform_observed_cny'];
const OP_FIELDS = ['parent_anchor_hash','risk_cny','local_estimate_cny','platform_observed_cny','fee_status','receipt_classification','output_limit_observed'];

export const isEmpiricalPolicy = (policy: ExecutionPolicy): policy is EmpiricalExecutionPolicy => policy.kind === KIND;
export const isEmpiricalExecution = (execution: ExecutionDetail) => execution.accounting_kind === KIND;
/** A prospective environment is not a frozen profile until the first quote is stored. */
export function executionIsPristine(execution: ExecutionDetail) {
  return execution.revision === 0 && execution.status === 'not_authorized' && execution.active_grant_id === null && execution.plan === null &&
    execution.quotes.length === 0 && execution.grants.length === 0 && execution.operations.length === 0 &&
    execution.role_workflow === undefined && execution.role_state === undefined && execution.accounting_kind === undefined && execution.parent_anchor_hash === undefined &&
    execution.ledger.accounting_kind === undefined && execution.ledger.call_count === 0 &&
    execution.ledger.held_cny === '0.000000' && execution.ledger.locally_accounted_cny === '0.000000';
}
export function executionPolicyMatchesProfile(execution: ExecutionDetail, policy: ExecutionPolicy) {
  if (executionIsPristine(execution)) return true;
  return isEmpiricalExecution(execution) === isEmpiricalPolicy(policy) && execution.role_workflow === policy.role_workflow;
}
/** Complete HTTP/text receipts cannot bypass the server-owned platform fee gate. */
export function empiricalExecutionBlocked(execution: ExecutionDetail) {
  return isEmpiricalExecution(execution) && execution.operations.some(op => op.fee_status !== 'matched' || op.receipt_classification !== 'complete');
}

/** These checks compare public metadata. The server still verifies authority, evidence and hashes. */
export class ExecutionRiskReader {
  constructor(private invalid: () => never) {}
  private group(value: Record<string, unknown>, fields: string[]) {
    if (!['accounting_kind', ...fields].some(field => present(value, field))) return false;
    if (value.accounting_kind !== KIND || fields.some(field => !present(value, field))) return this.invalid();
    return true;
  }
  policy(value: Record<string, unknown>) {
    const fields = ['per_call_risk_cny','risk_basis','parent_anchor_hash'];
    if (!['kind', ...fields].some(field => present(value, field)) && value.privacy_scope !== EMPIRICAL_EXECUTION_SCOPE) return false;
    if (value.kind !== KIND || value.privacy_scope !== EMPIRICAL_EXECUTION_SCOPE || value.risk_basis !== 'empirical_not_hard_billing_bound' ||
      value.per_call_risk_cny !== '1.000000' || !hash(value.parent_anchor_hash) || value.role_workflow !== EXECUTION_ROLE_WORKFLOW ||
      value.wire_api !== 'chat_completions' || !['max_tokens','max_completion_tokens'].includes(String(value.chat_output_parameter)) ||
      !count(value.max_calls) || value.max_calls < 1 || value.max_calls > 6 ||
      ['billing_bound_verified','input_bound_basis','max_input_tokens'].some(field => present(value, field))) return this.invalid();
    return true;
  }
  quote(value: Record<string, unknown>, policy: ExecutionPolicy): Partial<ExecutionQuote> {
    const tagged = this.group(value, ['parent_anchor_hash','per_call_risk_cny']);
    if (tagged !== isEmpiricalPolicy(policy)) return this.invalid();
    if (!tagged || !isEmpiricalPolicy(policy)) return {};
    if (value.parent_anchor_hash !== policy.parent_anchor_hash || value.per_call_risk_cny !== policy.per_call_risk_cny ||
      value.per_call_hold_cny !== policy.per_call_risk_cny || value.max_calls !== policy.max_calls || value.total_limit_cny !== policy.task_limit_cny) return this.invalid();
    return { accounting_kind: KIND, parent_anchor_hash: policy.parent_anchor_hash, per_call_risk_cny: policy.per_call_risk_cny };
  }
  grant(value: Record<string, unknown>): Partial<ExecutionGrant> {
    const tagged = this.group(value, ['parent_anchor_hash']);
    if (tagged !== (value.scope === EMPIRICAL_EXECUTION_SCOPE)) return this.invalid();
    if (!tagged) return {};
    if (!hash(value.parent_anchor_hash) || value.role_workflow !== EXECUTION_ROLE_WORKFLOW || !count(value.max_calls) || value.max_calls < 1 || value.max_calls > 6) return this.invalid();
    return { accounting_kind: KIND, parent_anchor_hash: value.parent_anchor_hash };
  }
  ledger(value: Record<string, unknown>): Partial<ExecutionLedger> {
    if (!this.group(value, LEDGER_FIELDS)) return {};
    if (!count(value.opening_call_count) || LEDGER_FIELDS.filter(field => field !== 'opening_call_count' && !NULLABLE_TOTALS.includes(field)).some(field => !money(value[field])) ||
      NULLABLE_TOTALS.some(field => !nullableMoney(value[field])) ||
      value.held_cny !== value.risk_allocated_cny || value.local_estimate_cny !== null && value.locally_accounted_cny !== value.local_estimate_cny ||
      value.account_held_cny !== value.account_risk_allocated_cny || value.account_local_estimate_cny !== null && value.account_locally_accounted_cny !== value.account_local_estimate_cny) return this.invalid();
    return { accounting_kind: KIND, opening_exposure_cny: value.opening_exposure_cny as string, opening_call_count: value.opening_call_count,
      risk_allocated_cny: value.risk_allocated_cny as string, local_estimate_cny: value.local_estimate_cny as string | null, platform_observed_cny: value.platform_observed_cny as string | null,
      account_risk_allocated_cny: value.account_risk_allocated_cny as string, account_local_estimate_cny: value.account_local_estimate_cny as string | null, account_platform_observed_cny: value.account_platform_observed_cny as string | null };
  }
  operation(value: Record<string, unknown>): Partial<ExecutionOperation> {
    if (!this.group(value, OP_FIELDS)) return {};
    if (!hash(value.parent_anchor_hash) || value.role_workflow !== EXECUTION_ROLE_WORKFLOW || value.risk_cny !== '1.000000' || value.hold_cny !== value.risk_cny ||
      value.wire_api !== 'chat_completions' || !present(value, 'chat_output_parameter') ||
      !nullableMoney(value.local_estimate_cny) || !nullableMoney(value.platform_observed_cny) || value.locally_accounted_cny !== value.local_estimate_cny ||
      !['awaiting_receipt','awaiting_platform_fee','matched','overrun'].includes(String(value.fee_status)) ||
      !(value.receipt_classification === null || ['complete','truncated','unconfirmed'].includes(String(value.receipt_classification))) ||
      !(value.output_limit_observed === null || typeof value.output_limit_observed === 'boolean')) return this.invalid();
    const observed = value.platform_observed_cny;
    const exceedsRisk = [value.local_estimate_cny, observed].some(amount => amount !== null && units(amount) > units(value.risk_cny as string));
    if ((value.fee_status === 'overrun') !== exceedsRisk || value.fee_status === 'matched' && observed === null ||
      !['matched','overrun'].includes(String(value.fee_status)) && observed !== null) return this.invalid();
    return { accounting_kind: KIND, parent_anchor_hash: value.parent_anchor_hash, risk_cny: value.risk_cny,
      local_estimate_cny: value.local_estimate_cny, platform_observed_cny: observed,
      fee_status: value.fee_status as ExecutionOperation['fee_status'], receipt_classification: value.receipt_classification as ExecutionOperation['receipt_classification'], output_limit_observed: value.output_limit_observed };
  }
  detail(value: Record<string, unknown>, execution: ExecutionDetail): Partial<ExecutionDetail> {
    const tagged = this.group(value, ['parent_anchor_hash']);
    const nested = [...execution.quotes, ...execution.grants, ...execution.operations, execution.ledger];
    const empiricalEnvironment = ['empirical_live','synthetic_empirical_test'].includes(execution.environment);
    if (nested.some(item => (item.accounting_kind === KIND) !== tagged) ||
      tagged !== empiricalEnvironment && !(empiricalEnvironment && !tagged && executionIsPristine(execution))) return this.invalid();
    if (!tagged) return {};
    if (!hash(value.parent_anchor_hash) || execution.role_workflow !== EXECUTION_ROLE_WORKFLOW ||
      [...execution.quotes, ...execution.grants, ...execution.operations].some(item => item.parent_anchor_hash !== value.parent_anchor_hash)) return this.invalid();
    const ledger = execution.ledger, operations = execution.operations, state = execution.role_state!;
    for (const grant of execution.grants) {
      const quote = execution.quotes.find(quote => quote.id === grant.quote_id);
      if (!quote || grant.policy_hash !== quote.policy.policy_hash || grant.disclosure_hash !== quote.disclosure_hash ||
        grant.max_calls !== quote.max_calls || grant.total_limit_cny !== quote.total_limit_cny) return this.invalid();
    }
    const validTotal = (total: string | null | undefined, values: Array<string | null | undefined>) => values.some(value => value == null) ? total === null : total != null && units(total) === sum(values);
    if (ledger.call_count !== operations.length || ledger.call_count > 6 || ledger.max_calls !== null && (ledger.max_calls > 6 || ledger.call_count > ledger.max_calls) ||
      units(ledger.risk_allocated_cny!) !== BigInt(operations.length) * BigInt(1000000) ||
      !validTotal(ledger.local_estimate_cny, operations.map(op => op.local_estimate_cny)) ||
      !validTotal(ledger.platform_observed_cny, operations.map(op => op.platform_observed_cny)) ||
      ledger.account_call_count < ledger.opening_call_count! + ledger.call_count ||
      units(ledger.account_risk_allocated_cny!) !== units(ledger.opening_exposure_cny!) + BigInt(ledger.account_call_count - ledger.opening_call_count!) * BigInt(1000000) ||
      ledger.local_estimate_cny === null && ledger.account_local_estimate_cny !== null || ledger.platform_observed_cny === null && ledger.account_platform_observed_cny !== null ||
      ledger.account_local_estimate_cny != null && ledger.local_estimate_cny != null && units(ledger.account_local_estimate_cny) < units(ledger.local_estimate_cny) ||
      ledger.account_platform_observed_cny != null && ledger.platform_observed_cny != null && units(ledger.account_platform_observed_cny) < units(ledger.platform_observed_cny)) return this.invalid();
    for (let index = 0; index < operations.length; index++) {
      const op = operations[index], q = execution.quotes.find(q => q.id === op.quote_id), g = execution.grants.find(g => g.id === op.grant_id);
      if (!q || !g || g.quote_id !== q.id || op.policy_hash !== q.policy.policy_hash || g.policy_hash !== op.policy_hash ||
        index < operations.length - 1 && (op.fee_status !== 'matched' || op.receipt_classification !== 'complete')) return this.invalid();
      if (op.fee_status !== 'matched' || op.receipt_classification !== 'complete') {
        if (op.role === 'planner' && execution.plan || op.role === 'reviewer' && state.first_verdict !== null ||
          op.role === 'repairer' && state.plans.length === 2 || op.role === 'rechecker' && state.recheck_verdict !== null ||
          execution.allowed_actions.some(action => action !== 'stop')) return this.invalid();
      }
    }
    return { accounting_kind: KIND, parent_anchor_hash: value.parent_anchor_hash };
  }
}
