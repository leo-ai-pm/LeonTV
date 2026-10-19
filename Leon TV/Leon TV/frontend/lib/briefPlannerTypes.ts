import type { CreatorProduction, CreatorBriefDetail } from './creatorBriefTypes';

/** Exact public D08 v1 projections; no model messages or execution authorization. */
export type PlannerMode = 'normal' | 'offline_test';
export type PlannerScenario = 'success' | 'repair' | 'invalid_tool' | 'step_limit' | 'response_unknown';
export type PlannerAction = 'stop' | 'resume';
export type PlannerStatus = 'awaiting_authorization' | 'ready' | 'queued' | 'running' | 'stopped' | 'response_unknown' | 'plan_ready' | 'blocked';
export interface PlannerSource {
  brief_id: string; brief_revision: number; source_version: number; source_hash: string; snapshot_hash: string;
  title: string; production: CreatorProduction; budget_cny: string | null; currency: 'CNY'; frozen_at: string;
}
export interface PlannerFrozenBrief extends PlannerSource { script: string; requirements: string }
export interface PlannerShot { id: string; duration_ms: number; visual: string; camera: string; audio: string; depends_on: string[] }
export interface PlannerPlan { source_snapshot_hash: string; duration_ms: 15000; ratio: '9:16'; shots: PlannerShot[] }
export interface PlannerSummary {
  id: string; revision: number; mode: PlannerMode; scenario: PlannerScenario | null; status: PlannerStatus;
  blocked_reason: string | null; allowed_actions: PlannerAction[]; created_at: string; updated_at: string; source: PlannerSource;
  authorization_status: 'not_authorized' | 'offline_only'; execution_enabled: false; quality_status: 'not_reviewed';
  step_count: number; repair_count: number; pending_call: { operation_id: string; status: 'intent' | 'received' } | null; plan_version: number;
}
export interface PlannerDetail extends PlannerSummary {
  client_request_key: string;
  limits: { max_steps: 6; max_repairs: 2; max_output_bytes: 65536; max_context_bytes: 1048576 };
  plan: PlannerPlan | null; plan_versions: Array<{ version: number; plan_hash: string; created_at: string }>;
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
