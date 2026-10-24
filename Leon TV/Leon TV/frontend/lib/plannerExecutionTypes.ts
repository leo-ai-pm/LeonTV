import type { PlannerPlan, PlannerRepairBinding, PlannerReviewBinding, PlannerReviewVerdict, PlannerRevisionCycleSummary, PlannerSource } from './briefPlannerTypes';

/** D09 v1 + additive D10/D11 v1. Source, authorization and money remain server-owned. */
export const EXECUTION_SCOPE = 'frozen_brief_and_tool_history_to_named_provider' as const;
export const EXECUTION_ROLE_WORKFLOW = 'bounded_revision_v1' as const;
export const EXECUTION_ROLE_SCOPE = {
  version: EXECUTION_ROLE_WORKFLOW, roles: ['planner','reviewer','repairer','rechecker'],
  data: ['frozen_source','plan_v1','first_review_report','repair_task','candidate_v2','prior_findings'],
  max_total_calls: 6, max_reviewer_calls: 1, max_repairer_calls: 1, max_rechecker_calls: 1, max_plan_version: 2,
} as const;
export type ExecutionRoleScope = typeof EXECUTION_ROLE_SCOPE;
export type ExecutionRole = 'planner' | 'reviewer' | 'repairer' | 'rechecker';
export interface ExecutionRoleState {
  schema_version: 1; status: PlannerRevisionCycleSummary['status']; roles: ['planner','reviewer','repairer','rechecker'];
  planner_call_count: number; reviewer_call_count: number; repairer_call_count: number; rechecker_call_count: number;
  max_total_calls: 6; max_reviewer_calls: 1; max_repairer_calls: 1; max_rechecker_calls: 1;
  first_review_binding: PlannerReviewBinding | null; first_report_hash: string | null; first_verdict: PlannerReviewVerdict | null;
  repair_binding: PlannerRepairBinding | null; repair_task_hash: string | null;
  recheck_binding: PlannerReviewBinding | null; recheck_report_hash: string | null; recheck_verdict: PlannerReviewVerdict | null;
  accepted_plan_version: 1 | 2 | null; plans: Array<{ version: 1 | 2; plan_hash: string }>;
}
export type ExecutionWireApi = 'chat_completions' | 'responses';
export type ExecutionChatOutputParameter = 'max_tokens' | 'max_completion_tokens';
export type ExecutionResponseStatus = 'completed' | 'incomplete' | 'failed' | 'in_progress' | 'queued' | 'cancelled';
export type ExecutionAction = 'resume' | 'stop' | 'reconcile';
export type ExecutionStatus = 'not_authorized' | 'ready' | 'queued' | 'running' | 'stopped' | 'blocked' | 'needs_reconciliation' | 'plan_ready';
export interface ExecutionPolicy {
  role_workflow?: typeof EXECUTION_ROLE_WORKFLOW;
  wire_api: ExecutionWireApi;
  /** Chat only; the normalized legacy Chat projection supplies max_tokens. */
  chat_output_parameter?: ExecutionChatOutputParameter;
  id: string; version: string; policy_hash: string; provider: 'openai_compatible'; model: string; endpoint: string; currency: 'CNY';
  input_cny_per_million: string; output_cny_per_million: string; max_input_tokens: number; max_output_tokens: number;
  max_input_bytes: number; max_output_bytes: number; request_deadline_seconds: number; max_calls: number;
  task_limit_cny: string; account_limit_cny: string; account_max_calls: number; valid_until: string; privacy_scope: typeof EXECUTION_SCOPE;
  pricing_basis: string; input_bound_basis: string;
}
export interface ExecutionPolicyResult { available: boolean; blocked_reason: string | null; environment: string; policy: ExecutionPolicy | null }
export interface ExecutionQuote {
  role_workflow?: typeof EXECUTION_ROLE_WORKFLOW; role_scope?: ExecutionRoleScope;
  id: string; task_id: string; planner_task_revision: number; snapshot_hash: string; policy: ExecutionPolicy;
  per_call_hold_cny: string; total_limit_cny: string; max_calls: number; expires_at: string; disclosure_hash: string; created_at: string;
  status: 'valid' | 'expired' | 'policy_stale';
}
export interface ExecutionGrant {
  role_workflow?: typeof EXECUTION_ROLE_WORKFLOW; role_scope_hash?: string;
  id: string; task_id: string; quote_id: string; snapshot_hash: string; policy_hash: string; disclosure_hash: string; scope: typeof EXECUTION_SCOPE;
  status: 'active' | 'expired' | 'revoked' | 'policy_stale'; total_limit_cny: string; max_calls: number; expires_at: string; created_at: string; revoked_at: string | null;
}
export interface ExecutionLedger {
  currency: 'CNY'; locally_accounted_cny: string; held_cny: string; task_limit_cny: string | null; call_count: number; max_calls: number | null;
  account_locally_accounted_cny: string; account_held_cny: string; account_limit_cny: string | null; account_call_count: number;
  account_max_calls: number | null; provider_bill_confirmed: false;
}
export interface ExecutionOperation {
  role_workflow?: typeof EXECUTION_ROLE_WORKFLOW; role?: ExecutionRole; role_call_index?: number; role_binding_hash?: string;
  wire_api: ExecutionWireApi; response_status: ExecutionResponseStatus | null;
  /** Belongs to this operation's frozen policy, never the currently active one. */
  chat_output_parameter?: ExecutionChatOutputParameter;
  cached_input_tokens: number | null; reasoning_output_tokens: number | null;
  id: string; step: number; status: 'submitting' | 'received' | 'settled' | 'needs_reconciliation'; grant_id: string; quote_id: string;
  policy_hash: string; context_hash: string; hold_cny: string; locally_accounted_cny: string | null;
  provider_request_id: string | null; provider_response_id: string | null; input_tokens: number | null; output_tokens: number | null;
  finish_reason: string | null; receipt_status: 'none' | 'received' | 'confirmed_local' | 'unconfirmed'; created_at: string; received_at: string | null;
}
export interface ExecutionDetail {
  role_workflow?: typeof EXECUTION_ROLE_WORKFLOW; role_state?: ExecutionRoleState;
  task_id: string; revision: number; environment: string; status: ExecutionStatus; blocked_reason: string | null; allowed_actions: ExecutionAction[];
  source: PlannerSource; policy_available: boolean; active_grant_id: string | null; quotes: ExecutionQuote[]; grants: ExecutionGrant[];
  ledger: ExecutionLedger; operations: ExecutionOperation[]; plan: PlannerPlan | null; quality_status: 'not_reviewed'; created_at: string | null; updated_at: string | null;
}
export interface ExecutionEvent { sequence: number; type: string; execution_revision: number; created_at: string; operation_id: string | null; code: string | null }
export interface ExecutionQuoteRequest { client_request_key: string; expected_execution_revision: number; expected_task_revision: number }
export interface ExecutionGrantRequest {
  client_request_key: string; expected_execution_revision: number; quote_id: string; accepted_disclosure_hash: string;
  accepted_scope: typeof EXECUTION_SCOPE; consent: true;
}
export interface ExecutionRevokeRequest { client_action_key: string; expected_execution_revision: number }
export interface ExecutionActionRequest extends ExecutionRevokeRequest { action: ExecutionAction }
export interface ExecutionWrite { execution: ExecutionDetail; replayed: boolean; applied_revision: number; quote?: ExecutionQuote; grant?: ExecutionGrant }
export type ExecutionIntent =
  | { version: 1; kind: 'quote'; task_id: string; snapshot_hash: string; request: ExecutionQuoteRequest }
  | { version: 1; kind: 'grant'; task_id: string; snapshot_hash: string; request: ExecutionGrantRequest }
  | { version: 1; kind: 'revoke'; task_id: string; snapshot_hash: string; grant_id: string; request: ExecutionRevokeRequest }
  | { version: 1; kind: 'action'; task_id: string; snapshot_hash: string; request: ExecutionActionRequest };
