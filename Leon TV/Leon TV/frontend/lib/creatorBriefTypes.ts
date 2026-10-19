/** D07 v1: docs/CREATOR-BRIEF-API.md. This feature cannot authorize or execute. */
export interface CreatorProduction { duration_seconds: 15; ratio: '9:16'; style: 'realistic_cinematic' }
export interface CreatorContent {
  title: string; script: string; requirements: string; production: CreatorProduction; budget_cny: string | null;
}
export interface CreatorCreateRequest extends CreatorContent { client_request_key: string }
export interface CreatorUpdateRequest extends CreatorContent { client_update_key: string; expected_revision: number }
export interface CreatorBriefSummary {
  id: string; revision: number; title: string; production: CreatorProduction; budget_cny: string | null;
  currency: 'CNY'; budget_status: 'pending' | 'draft'; authorization_status: 'not_authorized'; execution_enabled: false;
  source_hash: string; source_version: number; script_characters: number; created_at: string; updated_at: string;
}
export interface CreatorBriefDetail extends CreatorBriefSummary {
  script: string; requirements: string; client_request_key: string; current_revision: number; is_current: boolean;
  versions: Array<{ revision: number; source_version: number; source_hash: string; saved_at: string }>;
}
export interface CreatorBriefWriteResult { brief: CreatorBriefDetail; replayed: boolean; applied_revision: number }
export interface CreatorBriefList { briefs: CreatorBriefSummary[]; next_offset: number | null; has_more: boolean }
export interface CreatorEditorFields { title: string; script: string; requirements: string; budgetText: string }
export const EMPTY_CREATOR_FIELDS: CreatorEditorFields = { title: '', script: '', requirements: '', budgetText: '' };
