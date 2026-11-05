import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: handoff } = await loadModule('lib/creatorHandoff.ts');

function brief(patch = {}) {
  return {
    id: 'a'.repeat(32), revision: 3, title: '海边来信', production: { duration_seconds: 15, ratio: '9:16', style: 'realistic_cinematic' },
    budget_cny: null, currency: 'CNY', budget_status: 'pending', authorization_status: 'not_authorized', execution_enabled: false,
    source_hash: 'b'.repeat(64), source_version: 3, script_characters: 12, created_at: '2026-11-05T00:00:00Z', updated_at: '2026-11-05T00:00:00Z',
    script: '少女在海边等一封信。', requirements: '', client_request_key: 'k', current_revision: 3, is_current: true, versions: [],
    ...patch,
  };
}

test('a current brief is handed off with its script as the six-stage idea', () => {
  const result = handoff.buildCreatorHandoff(brief());
  assert.equal(result.idea, '少女在海边等一封信。');
  assert.equal(result.briefId, 'a'.repeat(32));
  assert.equal(result.revision, 3);
  assert.equal(result.title, '海边来信');
  assert.equal(result.videoRatio, '9:16');
});

test('requirements are appended so the six-stage entry keeps the production constraints', () => {
  const result = handoff.buildCreatorHandoff(brief({ requirements: '自然光，不要旁白。' }));
  assert.equal(result.idea, '少女在海边等一封信。\n\n制作要求：\n自然光，不要旁白。');
});

test('script and requirements are trimmed, and a blank title falls back to a placeholder', () => {
  const result = handoff.buildCreatorHandoff(brief({ title: '   ', script: '  第一幕。 ', requirements: '  低饱和 ' }));
  assert.equal(result.idea, '第一幕。\n\n制作要求：\n低饱和');
  assert.equal(result.title, '未命名创作简报');
});

test('a superseded or non-current brief is never handed off', () => {
  assert.equal(handoff.buildCreatorHandoff(brief({ is_current: false })), null);
  assert.equal(handoff.buildCreatorHandoff(brief({ is_current: false, current_revision: 4 })), null);
});

test('an empty script is never handed off', () => {
  assert.equal(handoff.buildCreatorHandoff(brief({ script: '   \n  ' })), null);
  assert.equal(handoff.buildCreatorHandoff(brief({ script: '' })), null);
});

test('the handoff link carries only the brief id, never the script body', () => {
  const href = handoff.creatorHandoffHref('a'.repeat(32));
  assert.equal(href, `/workflow?brief=${'a'.repeat(32)}`);
  assert.ok(!href.includes('少女'));
  assert.equal(handoff.creatorHandoffHref('a b/c'), '/workflow?brief=a%20b%2Fc');
});
