import { MEDIA_AUTHORITIES, MEDIA_COMPLETIONS, MEDIA_ERRORS, MEDIA_FILES, MEDIA_GENERATIONS, MEDIA_RECEPTIONS, type PlannerMediaBinding, type PlannerMediaObservation, type ProductionImageShot } from './plannerMediaTypes';

export class PlannerMediaError extends Error {
  constructor(public code: 'INVALID_RESPONSE' | 'READ_FAILED', message: string) { super(message); }
}
function invalid(): never { throw new PlannerMediaError('INVALID_RESPONSE', '分镜图片响应与当前任务或接口约定不一致，当前结果未核验。'); }
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}
const id = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const money = (value: unknown): value is string => typeof value === 'string' && /^\d+\.\d{6}$/.test(value);
function nullableId(value: unknown): string | null { if (value === null || id(value)) return value; return invalid(); }
function boolean(value: unknown): boolean { if (typeof value === 'boolean') return value; return invalid(); }
function nullableBoolean(value: unknown): boolean | null { return value === null ? null : boolean(value); }
function literal<T extends string>(value: unknown, options: readonly T[]): T {
  if (typeof value !== 'string' || !options.includes(value as T)) return invalid();
  return value as T;
}
function readShot(value: unknown): ProductionImageShot {
  const s = object(value);
  if (typeof s.shot_id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(s.shot_id) || !count(s.first_frame_version) || s.first_frame_version < 1 ||
    !(s.http_status === null || count(s.http_status) && s.http_status >= 100 && s.http_status <= 599) ||
    s.retry_authorized !== false || s.quality !== 'not_reviewed' || s.human_acceptance !== 'pending') return invalid();
  const shot: ProductionImageShot = {
    shot_id: s.shot_id, first_frame_version: s.first_frame_version, operation_id: nullableId(s.operation_id), query_id: nullableId(s.query_id),
    opt_in: boolean(s.opt_in), generation: literal(s.generation, MEDIA_GENERATIONS), completion: literal(s.completion, MEDIA_COMPLETIONS),
    reception: literal(s.reception, MEDIA_RECEPTIONS), received: nullableBoolean(s.received), persisted: nullableBoolean(s.persisted),
    delivery_registered: boolean(s.delivery_registered), delivery_origin: literal(s.delivery_origin, ['none', 'legacy', 'managed']),
    historical_saved: boolean(s.historical_saved), file_availability: literal(s.file_availability, MEDIA_FILES), authority: literal(s.authority, MEDIA_AUTHORITIES),
    fee_observed: boolean(s.fee_observed), http_status: s.http_status, error_code: s.error_code === null ? null : literal(s.error_code, MEDIA_ERRORS),
    needs_input: boolean(s.needs_input), retry_authorized: false, quality: 'not_reviewed', human_acceptance: 'pending',
  };
  // Reject contradictory success metadata; historical registration is a separate fact.
  const completeSaved = shot.file_availability === 'available' && shot.delivery_origin === 'managed' && shot.delivery_registered &&
    shot.reception === 'saved' && shot.historical_saved && shot.completion === 'completed' && !shot.needs_input;
  if (shot.query_id !== null && shot.operation_id === null || shot.completion === 'completed' && shot.generation !== 'accepted' ||
    shot.delivery_registered !== (shot.delivery_origin !== 'none') ||
    shot.persisted === true && (shot.received !== true || shot.file_availability !== 'available') ||
    shot.file_availability === 'available' && (!shot.opt_in || shot.received !== true || shot.persisted !== true) ||
    (shot.file_availability === 'available' || shot.delivery_origin === 'managed') && (shot.operation_id === null || shot.query_id === null || shot.completion !== 'completed') ||
    ['missing', 'invalid'].includes(shot.file_availability) && (shot.received !== false || shot.persisted !== false) ||
    shot.delivery_origin === 'legacy' && (shot.opt_in || shot.received !== null || shot.persisted !== null || shot.file_availability !== 'not_verified') ||
    shot.delivery_origin === 'managed' && !shot.opt_in ||
    completeSaved && shot.error_code !== null ||
    shot.error_code === 'FILE_MISSING' && shot.file_availability !== 'missing' ||
    shot.error_code === 'FILE_INVALID' && shot.file_availability !== 'invalid') return invalid();
  return shot;
}

/** Public pure decoder for actual ASGI fixtures. Never derives success from media.operations.delivered. */
export function readPlannerMedia(value: unknown, binding: PlannerMediaBinding): PlannerMediaObservation {
  if (!id(binding.task_id) || !hash(binding.source_snapshot_hash)) return invalid();
  const envelope = object(value), p = object(envelope.production_images), m = object(envelope.media);
  if (p.schema_version !== 1 || !id(p.task_id) || p.task_id !== binding.task_id || !hash(p.source_snapshot_hash) || p.source_snapshot_hash !== binding.source_snapshot_hash ||
    p.read_only !== true || !Array.isArray(p.shots) || !count(p.required_count) || !count(p.registered_count) || !count(p.verified_file_count) || !count(p.needs_input_count) ||
    !money(m.retained_exposure_cny) || !(m.platform_observed_cny === null || money(m.platform_observed_cny)) || m.provider_bill_confirmed !== false || m.risk_released !== false) return invalid();
  const scope = nullableId(p.scope_id), evidence = p.evidence_kind === null ? null : literal(p.evidence_kind, ['synthetic', 'empirical_receipts']);
  const status = literal(p.status, ['not_configured', 'observed', 'attention']);
  const shots = p.shots.map(readShot);
  if (new Set(shots.map(s => s.shot_id)).size !== shots.length || p.required_count !== shots.length ||
    p.registered_count !== shots.filter(s => s.delivery_registered).length || p.verified_file_count !== shots.filter(s => s.file_availability === 'available').length ||
    p.needs_input_count !== shots.filter(s => s.needs_input).length ||
    (scope === null ? status !== 'not_configured' || evidence !== null || shots.length !== 0 : status === 'not_configured' || evidence === null) ||
    status === 'observed' && p.needs_input_count > 0) return invalid();
  return {
    production_images: { schema_version: 1, task_id: p.task_id, source_snapshot_hash: p.source_snapshot_hash, scope_id: scope, evidence_kind: evidence,
      status, read_only: true, required_count: p.required_count, registered_count: p.registered_count, verified_file_count: p.verified_file_count, needs_input_count: p.needs_input_count, shots },
    media: { retained_exposure_cny: m.retained_exposure_cny, platform_observed_cny: m.platform_observed_cny, provider_bill_confirmed: false, risk_released: false },
  };
}

export const plannerMediaApi = {
  async get(binding: PlannerMediaBinding, signal?: AbortSignal): Promise<PlannerMediaObservation | null> {
    if (!id(binding.task_id) || !hash(binding.source_snapshot_hash)) return invalid();
    const abort = new AbortController(), cancel = () => abort.abort();
    signal?.addEventListener('abort', cancel, { once: true });
    if (signal?.aborted) abort.abort();
    const timer = setTimeout(cancel, 15000);
    try {
      const response = await fetch(`/api/planner/tasks/${binding.task_id}/execution/media`, {
        method: 'GET', cache: 'no-store', credentials: 'same-origin', redirect: 'error', headers: { Accept: 'application/json' }, signal: abort.signal,
      });
      const value: unknown = await response.json();
      if (abort.signal.aborted) throw new PlannerMediaError('READ_FAILED', '分镜图片读取已中断，当前结果未核验。');
      if (!response.ok) {
        const detail = object(object(value).detail);
        if (response.status === 404 && detail.code === 'NOT_FOUND' && detail.message === 'Execution record not found') return null;
        throw new PlannerMediaError('READ_FAILED', '分镜图片读取失败，当前结果未核验。可以重新读取本地记录。');
      }
      return readPlannerMedia(value, binding);
    } catch (error) {
      if (error instanceof PlannerMediaError) throw error;
      throw new PlannerMediaError('READ_FAILED', '分镜图片读取中断或响应不可用，当前结果未核验。可以重新读取本地记录。');
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', cancel);
    }
  },
};
