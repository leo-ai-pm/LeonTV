/** D48 read-only projection. No execution, download or recovery capabilities. */
export interface PlannerMediaBinding { task_id: string; source_snapshot_hash: string }
export const MEDIA_GENERATIONS = ['not_started', 'reserved', 'submitting', 'unknown', 'accepted', 'rejected'] as const;
export const MEDIA_COMPLETIONS = ['not_observed', 'pending', 'completed', 'failed', 'unknown'] as const;
export const MEDIA_RECEPTIONS = ['not_enabled', 'not_started', 'receiving', 'saved', 'failed', 'unknown'] as const;
export const MEDIA_FILES = ['not_recorded', 'not_verified', 'not_published', 'available', 'missing', 'invalid'] as const;
export const MEDIA_AUTHORITIES = ['not_applicable', 'valid', 'expired', 'revoked', 'unavailable'] as const;
export const MEDIA_ERRORS = ['HTTP_NOT_200', 'NETWORK_OR_WORKER', 'INVALID_BYTES', 'LOCAL_STORAGE', 'INTERRUPTED_MISSING_BYTES', 'RESOURCE_LIMIT', 'TRUNCATED_RESPONSE', 'TIMEOUT', 'CAPACITY_EXCEEDED', 'AUTHORITY_EXPIRED', 'AUTHORITY_REVOKED', 'AUTHORITY_UNAVAILABLE', 'LEGACY_FILE_NOT_VERIFIED', 'FILE_MISSING', 'FILE_INVALID', 'PERSISTENCE_PENDING', 'DELIVERY_PENDING'] as const;
export interface ProductionImageShot {
  shot_id: string; first_frame_version: number; operation_id: string | null; query_id: string | null;
  opt_in: boolean; generation: typeof MEDIA_GENERATIONS[number]; completion: typeof MEDIA_COMPLETIONS[number];
  reception: typeof MEDIA_RECEPTIONS[number]; received: boolean | null; persisted: boolean | null;
  delivery_registered: boolean; delivery_origin: 'none' | 'legacy' | 'managed'; historical_saved: boolean;
  file_availability: typeof MEDIA_FILES[number]; authority: typeof MEDIA_AUTHORITIES[number];
  fee_observed: boolean; http_status: number | null; error_code: typeof MEDIA_ERRORS[number] | null;
  needs_input: boolean; retry_authorized: false; quality: 'not_reviewed'; human_acceptance: 'pending';
}
export interface ProductionImages extends PlannerMediaBinding {
  schema_version: 1; scope_id: string | null; evidence_kind: null | 'synthetic' | 'empirical_receipts';
  status: 'not_configured' | 'observed' | 'attention'; read_only: true;
  required_count: number; registered_count: number; verified_file_count: number; needs_input_count: number;
  shots: ProductionImageShot[];
}
export interface PlannerMediaObservation {
  production_images: ProductionImages;
  /** Existing media ledger totals; risk is retained exposure, never spend. */
  media: { retained_exposure_cny: string; platform_observed_cny: string | null; provider_bill_confirmed: false; risk_released: false };
}
