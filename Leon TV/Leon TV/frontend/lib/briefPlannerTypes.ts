import type { CreatorProduction, CreatorBriefDetail } from './creatorBriefTypes';

/** Exact public D08 v1 projections; no model messages or execution authorization. */
export type PlannerMode = 'normal' | 'offline_test';
export type PlannerReviewScenario = 'review_accepted' | 'review_rejected' | 'review_unknown' | 'review_response_unknown' | 'review_invalid';
export type PlannerRevisionScenario = 'revision_accepted' | 'revision_rejected' | 'revision_needs_input' | 'revision_invalid' | 'revision_response_unknown' | 'recheck_unknown' | 'recheck_response_unknown' | 'recheck_invalid';
export type PlannerScenario = 'success' | 'repair' | 'invalid_tool' | 'step_limit' | 'response_unknown' | PlannerReviewScenario | PlannerRevisionScenario;
export type PlannerAction = 'stop' | 'resume';
export type PlannerStatus = 'awaiting_authorization' | 'ready' | 'queued' | 'running' | 'stopped' | 'response_unknown' | 'plan_ready' | 'blocked';
export interface PlannerSource {
  brief_id: string; brief_revision: number; source_version: number; source_hash: string; snapshot_hash: string;
  title: string; production: CreatorProduction; budget_cny: string | null; currency: 'CNY'; frozen_at: string;
}
export interface PlannerFrozenBrief extends PlannerSource { script: string; requirements: string }
export interface PlannerShot { id: string; duration_ms: number; visual: string; camera: string; audio: string; depends_on: string[] }
export interface PlannerPlan { source_snapshot_hash: string; duration_ms: 15000; ratio: '9:16'; shots: PlannerShot[] }
export type PlannerReviewVerdict = 'accepted' | 'rejected' | 'unknown';
export type PlannerReviewRuleId = 'RV-F' | 'RV-T' | 'RV-C' | 'RV-A' | 'RV-G';
export interface PlannerReviewBinding {
  review_id: string; run_id: string; intent_id: string; task_id: string; plan_version: number;
  plan_hash: string; source_snapshot_hash: string; rule_version: 'D15-RV-v0.2'; rule_hash: string;
}
export interface PlannerReviewTarget { shot_id: string; field: 'id' | 'duration_ms' | 'visual' | 'camera' | 'audio' | 'depends_on' | '$' }
export interface PlannerReviewFinding {
  finding_id: string; prior_finding_id: null; rule_id: PlannerReviewRuleId; targets: PlannerReviewTarget[]; source_segment_ids: string[];
  problem: string; impact: string; repair_scope: PlannerReviewTarget[]; preserve: PlannerReviewTarget[]; recheck_condition: string;
}
export interface PlannerReviewRule {
  rule_id: PlannerReviewRuleId; verdict: PlannerReviewVerdict; checked_scope: string;
  evidence: Array<{ source_segment_ids: string[]; targets: PlannerReviewTarget[]; note: string }>;
  findings: PlannerReviewFinding[];
}
export interface PlannerReviewReport { binding: PlannerReviewBinding; rules: PlannerReviewRule[]; overall_verdict: PlannerReviewVerdict }
export interface PlannerRepairTask {
  kind: 'needs_input' | 'content_repair_ready';
  binding: { source_snapshot_hash: string; parent_plan_version: number; parent_plan_hash: string; review_id: string; report_hash: string;
    rule_version: 'D15-RV-v0.2'; rule_hash: string; next_plan_version: number };
  finding_refs: Array<{ rule_id: PlannerReviewRuleId; finding_id: string }>;
  repair_targets: PlannerReviewTarget[]; affected_shot_ids: string[]; preserve_targets: PlannerReviewTarget[];
  invariants: { shot_ids: string[]; duration_ms: 15000; ratio: '9:16'; source_snapshot_hash: string; rule_version: 'D15-RV-v0.2'; rule_hash: string };
}
export interface PlannerCollaborationSummary {
  schema_version: 1; evidence_kind: 'synthetic'; roles: ['planner', 'reviewer'];
  status: 'awaiting_plan' | 'review_ready' | 'review_running' | 'accepted' | 'rejected' | 'unknown' | 'response_unknown' | 'invalid';
  planner_call_count: number; reviewer_call_count: 0 | 1; max_total_calls: 6; max_reviewer_calls: 1; paid_call_limit: 0;
  binding: PlannerReviewBinding | null; report_hash: string | null;
}
export interface PlannerCollaborationDetail extends PlannerCollaborationSummary { report: PlannerReviewReport | null; repair_task: PlannerRepairTask | null }
export interface PlannerRepairBinding {
  task_id: string; run_id: string; intent_id: string; source_snapshot_hash: string; parent_plan_version: 1; parent_plan_hash: string;
  review_id: string; report_hash: string; repair_task_hash: string; next_plan_version: 2;
}
export interface PlannerRecheckFinding extends Omit<PlannerReviewFinding, 'prior_finding_id'> { prior_finding_id: string | null }
export interface PlannerRecheckRule extends Omit<PlannerReviewRule, 'findings'> { findings: PlannerRecheckFinding[] }
export interface PlannerFindingResolution {
  prior_finding_id: string; rule_id: PlannerReviewRuleId; status: 'resolved' | 'unresolved' | 'unknown'; source_segment_ids: string[];
  targets: PlannerReviewTarget[]; note: string;
}
export interface PlannerRecheckReport extends Omit<PlannerReviewReport, 'rules'> {
  parent_review: { review_id: string; plan_version: 1; plan_hash: string; report_hash: string };
  resolutions: PlannerFindingResolution[]; rules: PlannerRecheckRule[];
}
export interface PlannerRevisionCycleSummary {
  schema_version: 1; evidence_kind: 'synthetic'; roles: ['planner', 'reviewer', 'repairer', 'rechecker'];
  status: 'awaiting_plan' | 'review_ready' | 'review_running' | 'review_response_unknown' | 'review_invalid' | 'needs_input' |
    'repair_ready' | 'repair_running' | 'repair_invalid' | 'repair_response_unknown' | 'recheck_ready' | 'recheck_running' |
    'recheck_response_unknown' | 'recheck_invalid' | 'accepted' | 'rejected' | 'unknown';
  planner_call_count: number; reviewer_call_count: 0 | 1; repairer_call_count: 0 | 1; rechecker_call_count: 0 | 1;
  max_total_calls: 6; max_reviewer_calls: 1; max_repairer_calls: 1; max_rechecker_calls: 1; paid_call_limit: 0;
  first_review_binding: PlannerReviewBinding | null; first_report_hash: string | null; repair_binding: PlannerRepairBinding | null;
  recheck_binding: PlannerReviewBinding | null; recheck_report_hash: string | null; accepted_plan_version: 1 | 2 | null;
}
export interface PlannerRevisionCycleDetail extends PlannerRevisionCycleSummary {
  parent_plan: PlannerPlan | null; first_report: PlannerReviewReport | null; repair_task: PlannerRepairTask | null; recheck_report: PlannerRecheckReport | null;
}
export interface PlannerSummary {
  id: string; revision: number; mode: PlannerMode; scenario: PlannerScenario | null; status: PlannerStatus;
  blocked_reason: string | null; allowed_actions: PlannerAction[]; created_at: string; updated_at: string; source: PlannerSource;
  authorization_status: 'not_authorized' | 'offline_only'; execution_enabled: false; quality_status: 'not_reviewed';
  step_count: number; repair_count: number; pending_call: { operation_id: string; status: 'intent' | 'received' } | null; plan_version: number;
  collaboration?: PlannerCollaborationSummary;
  revision_cycle?: PlannerRevisionCycleSummary;
}
export interface PlannerDetail extends PlannerSummary {
  client_request_key: string;
  limits: { max_steps: 6; max_repairs: 2; max_output_bytes: 65536; max_context_bytes: 1048576 };
  plan: PlannerPlan | null; plan_versions: Array<{ version: number; plan_hash: string; created_at: string }>;
  collaboration?: PlannerCollaborationDetail;
  revision_cycle?: PlannerRevisionCycleDetail;
}
export interface PlannerEvent { sequence: number; type: string; task_revision: number; created_at: string; step: number | null; code: string | null }
export interface PlannerCreate { client_request_key: string; brief_id: string; brief_revision: number }
export interface PlannerOfflineCreate extends PlannerCreate { scenario: PlannerScenario }
export interface PlannerActionRequest { client_action_key: string; expected_revision: number; action: PlannerAction }
export interface PlannerWrite { task: PlannerDetail; replayed: boolean; applied_revision: number }
export interface PlannerBriefGate {
  document: CreatorBriefDetail | null; dirty: boolean; saving: boolean; loading: boolean;
  pending: unknown; conflictDetected: boolean;
}
export type PlannerIntent =
  | { version: 1; kind: 'create'; mode: PlannerMode; request: PlannerCreate | PlannerOfflineCreate }
  | { version: 1; kind: 'action'; task_id: string; request: PlannerActionRequest };
