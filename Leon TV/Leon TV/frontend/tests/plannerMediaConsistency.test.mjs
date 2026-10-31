import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: api } = await loadModule('lib/plannerMediaApi.ts');
const { module: view } = await loadModule('lib/plannerMediaView.ts');
const binding = { task_id: 'a'.repeat(32), source_snapshot_hash: 'b'.repeat(64) };
function fixture(patch = {}) {
  const shot = { shot_id: 'S01', first_frame_version: 1, operation_id: 'c'.repeat(32), query_id: 'd'.repeat(32),
    opt_in: true, generation: 'accepted', completion: 'completed', reception: 'saved', received: true, persisted: true,
    delivery_registered: true, delivery_origin: 'managed', historical_saved: true, file_availability: 'available', authority: 'valid',
    fee_observed: true, http_status: 200, error_code: null, needs_input: false, retry_authorized: false, quality: 'not_reviewed', human_acceptance: 'pending', ...patch };
  return { media: { retained_exposure_cny: '1.000000', platform_observed_cny: '0.040000', provider_bill_confirmed: false, risk_released: false },
    production_images: { schema_version: 1, ...binding, scope_id: 'e'.repeat(32), evidence_kind: 'synthetic', read_only: true,
      status: shot.needs_input ? 'attention' : 'observed', required_count: 1, registered_count: Number(shot.delivery_registered),
      verified_file_count: Number(shot.file_availability === 'available'), needs_input_count: Number(shot.needs_input), shots: [shot] } };
}

test('actual decoder rejects saved-success error codes and contradictory file errors even in attention state', () => {
  for (const error_code of ['FILE_INVALID', 'FILE_MISSING', 'HTTP_NOT_200', 'DELIVERY_PENDING', 'AUTHORITY_EXPIRED']) {
    const body = fixture({ error_code });
    assert.throws(() => api.readPlannerMedia(body, binding), { code: 'INVALID_RESPONSE' }, error_code);
    assert.notEqual(view.mediaShotPresentation(body.production_images.shots[0], true).label, '已保存');
  }
  for (const error_code of ['FILE_INVALID', 'FILE_MISSING']) {
    assert.throws(() => api.readPlannerMedia(fixture({ error_code, needs_input: true }), binding), { code: 'INVALID_RESPONSE' });
  }
});

test('actual decoder preserves valid partial, accurate file failure and expiry-independent availability', () => {
  const cases = [
    [{ reception: 'receiving', historical_saved: false, delivery_registered: false, delivery_origin: 'none', needs_input: true, error_code: 'DELIVERY_PENDING' }, '结果待确认'],
    [{ authority: 'expired' }, '已保存'],
    [{ authority: 'expired', delivery_registered: false, delivery_origin: 'none', needs_input: true, error_code: 'AUTHORITY_EXPIRED' }, '结果待确认'],
    [{ file_availability: 'missing', received: false, persisted: false, error_code: 'FILE_MISSING', needs_input: true }, '结果待确认'],
    [{ file_availability: 'invalid', received: false, persisted: false, error_code: 'FILE_INVALID', needs_input: true }, '结果待确认'],
  ];
  for (const [patch, expected] of cases) {
    const data = api.readPlannerMedia(fixture(patch), binding);
    assert.equal(view.mediaShotPresentation(data.production_images.shots[0], true).label, expected);
  }
});
