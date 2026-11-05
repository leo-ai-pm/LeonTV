import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: sp } = await loadModule('lib/storyboardPlan.ts');
const { module: canvas } = await loadModule('lib/briefToCanvas.ts');

const brief = {
  id: 'a'.repeat(32), revision: 1, title: '海边来信', production: { duration_seconds: 15, ratio: '9:16', style: 'realistic_cinematic' },
  budget_cny: null, currency: 'CNY', budget_status: 'pending', authorization_status: 'not_authorized', execution_enabled: false,
  source_hash: 'b'.repeat(64), source_version: 1, script_characters: 10, created_at: '2026-11-05T00:00:00Z', updated_at: '2026-11-05T00:00:00Z',
  script: '少女在海边等一封信。', requirements: '自然光，无旁白。', client_request_key: 'k', current_revision: 1, is_current: true, versions: [],
};

const good = {
  shots: [
    { title: '海边等待', description: '少女站在浅滩望向海面。', visualPrompt: '中景，清晨海边，米色风衣少女', duration: 5 },
    { title: '捡到信封', description: '潮水退去，沙里露出一只旧信封。', visualPrompt: '近景，湿沙与旧信封，低角度', duration: 5 },
    { title: '抬头看灯塔', description: '她抬头看向远处灯塔。', visualPrompt: '过肩镜头，远处灯塔，冷色调', duration: 5 },
  ],
};

test('the prompt pins shot count, total duration and the JSON contract', () => {
  const prompt = sp.buildStoryboardPrompt(brief, 3, 15);
  assert.ok(prompt.includes('恰好 3 个镜头'));
  assert.ok(prompt.includes('总时长 15 秒'));
  assert.ok(prompt.includes('只输出一个 JSON 对象'));
  assert.ok(prompt.includes('少女在海边等一封信。'));
  assert.ok(prompt.includes('制作要求：'));
});

test('a well-formed reply is accepted and normalized', () => {
  const result = sp.parseStoryboard(JSON.stringify(good), 3, 15);
  assert.equal(result.error, undefined);
  assert.equal(result.shots.length, 3);
  assert.deepEqual(result.shots.map(shot => shot.duration), [5, 5, 5]);
  assert.equal(result.shots[0].visualPrompt.length > 0, true);
});

test('fenced JSON with prose still parses', () => {
  const result = sp.parseStoryboard('好：\n```json\n' + JSON.stringify(good) + '\n```', 3, 15);
  assert.equal(result.shots.length, 3);
});

test('a shot-count mismatch is rejected as a whole', () => {
  const result = sp.parseStoryboard(JSON.stringify({ shots: good.shots.slice(0, 2) }), 3, 15);
  assert.equal(result.shots.length, 0);
  assert.match(result.error, /2 个镜头/);
});

test('a duration mismatch is rejected instead of silently rescaling', () => {
  const bad = { shots: good.shots.map(shot => ({ ...shot, duration: 4 })) };
  const result = sp.parseStoryboard(JSON.stringify(bad), 3, 15);
  assert.equal(result.shots.length, 0);
  assert.match(result.error, /总时长 12 秒/);
});

test('missing fields and bad durations are rejected', () => {
  const noPrompt = { shots: good.shots.map((shot, index) => (index === 1 ? { ...shot, visualPrompt: '' } : shot)) };
  assert.match(sp.parseStoryboard(JSON.stringify(noPrompt), 3, 15).error, /visualPrompt/);
  const badDuration = { shots: good.shots.map((shot, index) => (index === 0 ? { ...shot, duration: 0 } : shot)) };
  assert.match(sp.parseStoryboard(JSON.stringify(badDuration), 3, 15).error, /duration/);
});

test('unparseable output is reported, never guessed', () => {
  assert.match(sp.parseStoryboard('抱歉，我无法完成。', 3, 15).error, /没有返回可解析/);
  assert.match(sp.parseStoryboard('{"shots": [', 3, 15).error, /没有返回可解析/);
  assert.match(sp.parseStoryboard(JSON.stringify({ lines: [] }), 3, 15).error, /shots/);
});

test('real shots fill the canvas while image and video nodes stay empty', () => {
  const { shots } = sp.parseStoryboard(JSON.stringify(good), 3, 15);
  const plan = canvas.buildBriefCanvas(brief, 3, shots);
  const storyboards = plan.nodes.filter(node => node.data.kind === 'storyboard');
  const images = plan.nodes.filter(node => node.data.kind === 'image');
  const videos = plan.nodes.filter(node => node.data.kind === 'video');
  assert.deepEqual(storyboards.map(node => node.data.title), ['海边等待', '捡到信封', '抬头看灯塔']);
  assert.ok(storyboards.every(node => node.data.status === 'completed' && node.data.description.length > 0));
  assert.ok(images.every(node => !node.data.mediaPath && node.data.status === 'idle'));
  assert.ok(videos.every(node => !node.data.mediaPath && node.data.status === 'idle'));
  assert.deepEqual(videos.map(node => node.data.duration), [5, 5, 5]);
});

test('without content the canvas still falls back to an empty skeleton', () => {
  const plan = canvas.buildBriefCanvas(brief, 2);
  assert.ok(plan.nodes.filter(node => node.data.kind === 'storyboard').every(node => node.data.status === 'idle'));
});


test('generated image and video nodes inherit the brief aspect ratio', () => {
  const { shots } = sp.parseStoryboard(JSON.stringify(good), 3, 15);
  const plan = canvas.buildBriefCanvas(brief, 3, shots);
  const media = plan.nodes.filter(node => node.data.kind === 'image' || node.data.kind === 'video');
  assert.ok(media.length > 0);
  assert.ok(media.every(node => node.data.ratio === '9:16'));
  const videos = plan.nodes.filter(node => node.data.kind === 'video');
  assert.ok(videos.every(node => node.data.video_ratio === '9:16'));
});
