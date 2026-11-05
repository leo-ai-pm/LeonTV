import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: plan } = await loadModule('lib/briefToCanvas.ts');

const brief = {
  id: 'a'.repeat(32), revision: 2, title: '海边来信', production: { duration_seconds: 15, ratio: '9:16', style: 'realistic_cinematic' },
  budget_cny: null, currency: 'CNY', budget_status: 'pending', authorization_status: 'not_authorized', execution_enabled: false,
  source_hash: 'b'.repeat(64), source_version: 2, script_characters: 12, created_at: '2026-11-05T00:00:00Z', updated_at: '2026-11-05T00:00:00Z',
  script: '少女在海边等一封信。', requirements: '自然光，无旁白。', client_request_key: 'k', current_revision: 2, is_current: true, versions: [],
};

test('durations split the fixed 15 seconds into whole seconds', () => {
  assert.deepEqual(plan.splitDurations(15, 3), [5, 5, 5]);
  assert.deepEqual(plan.splitDurations(15, 4), [4, 4, 4, 3]);
  assert.deepEqual(plan.splitDurations(15, 2), [8, 7]);
  assert.deepEqual(plan.splitDurations(15, 1), [15]);
  assert.equal(plan.splitDurations(15, 3).reduce((a, b) => a + b, 0), 15);
});

test('shot counts are clamped to the supported range', () => {
  assert.equal(plan.clampShots(0), 1);
  assert.equal(plan.clampShots(99), 6);
  assert.equal(plan.clampShots(3.7), 3);
  assert.equal(plan.clampShots(Number.NaN), 3);
});

test('a brief becomes one chained storyboard/frame/clip column per shot', () => {
  const result = plan.buildBriefCanvas(brief, 3);
  assert.equal(result.nodes.length, 1 + 3 * 3);
  assert.equal(result.edges.length, 9);
  assert.equal(result.nodes[0].data.kind, 'text');
  assert.match(result.nodes[0].data.description, /少女在海边等一封信。/);
  assert.match(result.nodes[0].data.description, /制作要求：\n自然光，无旁白。/);
  assert.equal(result.title, '海边来信 · 画布');
});

test('generated placeholders carry no media and stay idle so nothing is silently claimed', () => {
  const result = plan.buildBriefCanvas(brief, 2);
  const media = result.nodes.filter(n => n.data.kind === 'image' || n.data.kind === 'video');
  assert.equal(media.length, 4);
  for (const node of media) {
    assert.equal(node.data.mediaPath, undefined);
    assert.equal(node.data.status, 'idle');
  }
  assert.ok(result.nodes.every(n => n.data.kind === 'text' || n.data.status === 'idle'));
});

test('each clip inherits its shot duration and the chain points brief → shot → frame → clip', () => {
  const result = plan.buildBriefCanvas(brief, 4);
  const clips = result.nodes.filter(n => n.data.kind === 'video');
  assert.deepEqual(clips.map(n => n.data.duration), [4, 4, 4, 3]);
  const targets = result.edges.map(e => `${e.source}>${e.target}`);
  assert.ok(targets.includes('brief>shot-1'));
  assert.ok(targets.includes('shot-4>frame-4'));
  assert.ok(targets.includes('frame-4>clip-4'));
});

test('out-of-range shot counts still produce a valid plan', () => {
  assert.equal(plan.buildBriefCanvas(brief, 0).nodes.length, 4);
  assert.equal(plan.buildBriefCanvas(brief, 50).nodes.length, 1 + 6 * 3);
});
