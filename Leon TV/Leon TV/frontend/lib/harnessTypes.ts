/** D06 v1: docs/HARNESS-RUNTIME-API.md. Test execution is not production acceptance. */
export type HarnessScenario = 'success' | 'repair' | 'review_unknown' | 'review_rejected' | 'unknown_acceptance' | 'zero_budget' | 'resume_checkpoint';
export type HarnessAction = 'stop' | 'resume' | 'reconcile' | 'revise';
export type HarnessQuality = 'unknown' | 'accepted' | 'rejected';
export interface HarnessCreateRequest {
  mode: 'test';
  client_request_key: string;
  project_id: string;
  source: { hash: string; record_ids: string[] };
  production: { ratio: '9:16'; duration_ms: 15000 };
  authorization_ref: 'test-zero-paid-v1';
  scenario: HarnessScenario;
}
export interface HarnessDependency { artifact_id: string; version: number; sha256: string }
export interface HarnessArtifact {
  id: string; version: number; kind: string; mode: 'test';
  generation_status: string; quality_status: HarnessQuality;
  required_scopes: string[]; dependencies: HarnessDependency[];
  sha256: string | null; media_url: string | null; review_ids: string[]; stale: boolean;
}
export interface HarnessReview {
  id: string; artifact_id: string; artifact_version: number; artifact_sha256: string;
  mode: 'test'; evidence_type: 'fixture'; status: HarnessQuality; scopes: Record<string, HarnessQuality>;
  rule_version: string; valid: boolean; reason: string | null; receipt_id: string | null;
}
export interface HarnessRun {
  schema_version: 1; id: string; mode: 'test'; test_mode: true; evidence_type: 'fixture';
  project_id: string | null; client_request_key: string; scenario: HarnessScenario;
  status: string; revision: number; snapshot_sequence: number;
  source: { hash: string; record_ids: string[] };
  production: { ratio: string; duration_ms: number };
  blocked_reason: string | null;
  plan: Array<{ id: string; depends_on: string[] }>;
  artifacts: HarnessArtifact[]; reviews: HarnessReview[];
  manifest: { version: number; final_artifact_id: string; final_version: number; final_sha256: string; review_bundle_hash: string; valid: boolean } | null;
  budget: { currency: string; paid_limit: string; paid_reserved: string; paid_actual: string; fixture_limit: number; fixture_used: number; unresolved_operations: number };
  metrics: Record<string, number>;
  final_review: Record<string, unknown> | null;
  allowed_actions: HarnessAction[];
  created_at: string; updated_at: string;
}
export interface HarnessActionRequest {
  mode: 'test'; client_action_key: string; expected_revision: number; action: HarnessAction; artifact_id?: string;
}
export interface HarnessReviewRequest {
  mode: 'test'; client_review_key: string; expected_revision: number; manifest_version: number;
  final_sha256: string; decision: 'accept' | 'return'; feedback: string;
}
export interface HarnessEvent {
  sequence: number; timestamp: string; run_id: string; mode: 'test'; type: string;
  operation_id?: string; tool_call_id?: string; artifact_id?: string; version?: number;
  dependencies?: HarnessDependency[]; generation_status?: string; quality_status?: HarnessQuality;
  reason?: string; receipt_id?: string; judge_id?: string; amount_cny?: string; usage?: unknown;
}
export interface HarnessEvents { mode: 'test'; events: HarnessEvent[]; next_sequence: number; has_more: boolean }
