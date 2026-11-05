import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: router } = await loadModule('lib/modelRouter.ts');

const catalog = {
  llm: [
    { provider: 'dashscope', models: [{ id: 'qwen3.7-max' }, { id: 'qwen3-max' }] },
    { provider: 'deepseek', models: [{ id: 'deepseek-chat' }, { id: 'deepseek-reasoner' }] },
    { provider: 'gemini', models: [{ id: 'gemini-2.5-flash' }] },
  ],
  vlm: [
    { provider: 'dashscope', models: [{ id: 'qwen3.5-plus' }] },
    { provider: 'gemini', models: [{ id: 'gemini-2.0-flash' }] },
  ],
  t2i: [
    { provider: 'tudou', models: [{ id: 'gpt-image-2-all' }] },
    { provider: 'ark', models: [{ id: 'doubao-seedream-5-0-260128' }] },
  ],
  i2i: [
    { provider: 'tudou', models: [{ id: 'gpt-image-2-all' }] },
  ],
  video: [
    { provider: 'tudou', models: [{ id: 'seedance-2.0' }] },
    { provider: 'dashscope', models: [{ id: 'wan2.7-i2v' }] },
  ],
};

const full = {
  dashscope: { api_key: 'k' },
  deepseek: { api_key: 'k' },
  tudou: { api_key: 'k' },
  gemini: { api_key: 'k' },
  ark: { api_key: 'k' },
};

test('with every key present the router prefers the verified paths', () => {
  const plan = router.planModels(catalog, full);
  assert.deepEqual(plan.missing, []);
  assert.deepEqual(plan.picks.llm, { provider: 'dashscope', model: 'qwen3.7-max' });
  assert.deepEqual(plan.picks.vlm, { provider: 'dashscope', model: 'qwen3.5-plus' });
  assert.deepEqual(plan.picks.t2i, { provider: 'tudou', model: 'gpt-image-2-all' });
  assert.deepEqual(plan.picks.video, { provider: 'tudou', model: 'seedance-2.0' });
});

test('deepseek becomes the LLM when dashscope has no key', () => {
  const plan = router.planModels(catalog, { deepseek: { api_key: 'k' }, tudou: { api_key: 'k' } });
  assert.deepEqual(plan.picks.llm, { provider: 'deepseek', model: 'deepseek-chat' });
  assert.equal(plan.picks.video.provider, 'tudou');
});

test('a missing capability is reported instead of guessed', () => {
  const plan = router.planModels(catalog, { deepseek: { api_key: 'k' }, tudou: { api_key: 'k' } });
  // DeepSeek has no vision model, and no other vision provider is configured.
  assert.deepEqual(plan.missing, ['vlm']);
  assert.equal(plan.picks.vlm, undefined);
});

test('an empty or missing credential never selects a provider', () => {
  const plan = router.planModels(catalog, { dashscope: { api_key: '' }, tudou: { api_key: 'k' } });
  assert.notEqual(plan.picks.llm?.provider, 'dashscope');
  assert.ok(plan.missing.includes('vlm'));
});

test('kling needs both access and secret keys', () => {
  assert.equal(router.hasCredentials('kling', { kling: { api_key: 'k' } }), false);
  assert.equal(router.hasCredentials('kling', { kling: { access_key: 'a', secret_key: 'b' } }), true);
});

test('a provider listed in the catalog but not configured is skipped', () => {
  const plan = router.planModels(catalog, { gemini: { api_key: 'k' } });
  assert.deepEqual(plan.picks.vlm, { provider: 'gemini', model: 'gemini-2.0-flash' });
  // 目录里有 tudou/dashscope 的图像与视频，但都没有密钥，因此必须报缺能力。
  assert.deepEqual(plan.missing.sort(), ['i2i', 't2i', 'video']);
});

test('a provider with an empty model list falls through to the next choice', () => {
  const plan = router.planModels({ ...catalog, video: [{ provider: 'tudou', models: [] }, { provider: 'dashscope', models: [{ id: 'wan2.7-i2v' }] }] },
    { tudou: { api_key: 'k' }, dashscope: { api_key: 'k' } });
  assert.deepEqual(plan.picks.video, { provider: 'dashscope', model: 'wan2.7-i2v' });
});
