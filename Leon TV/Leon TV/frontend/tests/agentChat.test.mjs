import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: chat } = await loadModule('lib/agentChat.ts');

const snapshot = {
  title: '海边来信',
  nodes: [
    { id: 'node-1', kind: 'image', title: '首帧', status: 'completed', mediaPath: 'result/x.png', description: '少女在海边' },
    { id: 'node-2', kind: 'video', title: '视频1', status: 'idle' },
  ],
};
const ids = new Set(['node-1', 'node-2']);

test('the prompt carries the canvas nodes and the JSON contract', () => {
  const prompt = chat.buildAgentPrompt(snapshot, '再补一个分镜');
  assert.ok(prompt.includes('id=node-1'));
  assert.ok(prompt.includes('id=node-2'));
  assert.ok(prompt.includes('只输出一个 JSON 对象'));
  assert.ok(prompt.includes('用户：再补一个分镜'));
  assert.ok(prompt.includes('不要编造素材'));
});

test('an empty canvas still produces a usable prompt', () => {
  const prompt = chat.buildAgentPrompt({ title: '', nodes: [] }, '开始');
  assert.ok(prompt.includes('(画布为空)'));
});

test('a plain JSON reply yields the reply and its actions', () => {
  const raw = JSON.stringify({
    reply: '好，已新增分镜。',
    actions: [{ type: 'add_node', kind: 'storyboard' }, { type: 'run_node', id: 'node-2' }],
  });
  const turn = chat.parseAgentReply(raw, ids);
  assert.equal(turn.reply, '好，已新增分镜。');
  assert.deepEqual(turn.actions, [
    { type: 'add_node', kind: 'storyboard' },
    { type: 'run_node', id: 'node-2' },
  ]);
});

test('code fences and surrounding prose are tolerated', () => {
  const raw = '好的：\n```json\n{"reply":"已删除","actions":[{"type":"delete_node","id":"node-1"}]}\n```';
  const turn = chat.parseAgentReply(raw, ids);
  assert.equal(turn.reply, '已删除');
  assert.deepEqual(turn.actions, [{ type: 'delete_node', id: 'node-1' }]);
});

test('unknown ids, unknown kinds and empty updates are dropped', () => {
  const raw = JSON.stringify({
    reply: 'done',
    actions: [
      { type: 'delete_node', id: 'not-on-canvas' },
      { type: 'add_node', kind: 'output' },
      { type: 'update_node', id: 'node-1' },
      { type: 'update_node', id: 'node-1', title: '  新名称  ' },
      { type: 'run_node' },
      { type: 'export' },
    ],
  });
  const turn = chat.parseAgentReply(raw, ids);
  assert.deepEqual(turn.actions, [
    { type: 'update_node', id: 'node-1', title: '新名称' },
    { type: 'export' },
  ]);
});

test('malformed output degrades to plain text with no actions', () => {
  assert.deepEqual(chat.parseAgentReply('模型直接说了句话，没有 JSON。', ids), {
    reply: '模型直接说了句话，没有 JSON。', actions: [],
  });
  assert.deepEqual(chat.parseAgentReply('{"reply": "坏掉的', ids), {
    reply: '{"reply": "坏掉的', actions: [],
  });
  assert.deepEqual(chat.parseAgentReply('', ids), { reply: '', actions: [] });
});

test('long titles and descriptions are clipped before they reach the canvas', () => {
  const turn = chat.parseAgentReply(JSON.stringify({
    reply: 'x',
    actions: [{ type: 'update_node', id: 'node-1', title: 'a'.repeat(400), description: 'b'.repeat(5000) }],
  }), ids);
  const action = turn.actions[0];
  assert.equal(action.type, 'update_node');
  assert.equal(action.title.length, 120);
  assert.equal(action.description.length, 2000);
});

test('actions are described with the node title for user-facing logs', () => {
  const names = new Map([['node-1', '首帧']]);
  assert.equal(chat.describeAction({ type: 'delete_node', id: 'node-1' }, names), '删除「首帧」');
  assert.equal(chat.describeAction({ type: 'add_node', kind: 'image' }, names), '新增参考图节点');
  assert.equal(chat.describeAction({ type: 'export' }, names), '导出成片');
});

test('actions are dispatched to the canvas in order and unsupported ones are skipped', async () => {
  const { module: busMod } = await loadModule('lib/canvasBus.ts');
  const bus = busMod.createCanvasBus();
  const calls = [];
  bus.register({
    addNode: kind => calls.push(`add:${kind}`),
    updateNode: (id, patch) => calls.push(`update:${id}:${patch.title || ''}`),
    deleteNode: id => calls.push(`delete:${id}`),
    runNode: id => calls.push(`run:${id}`),
    exportVideo: () => calls.push('export'),
    snapshot: () => ({ title: '', nodes: [] }),
  });
  const names = new Map([['node-1', '首帧']]);
  const applied = chat.applyAgentActions(bus, [
    { type: 'add_node', kind: 'video' },
    { type: 'update_node', id: 'node-1', title: '新名字' },
    { type: 'export' },
  ], names);
  assert.deepEqual(calls, ['add:video', 'update:node-1:新名字', 'export']);
  assert.deepEqual(applied, ['新增视频节点', '修改「首帧」', '导出成片']);
});


test('add_node carries an optional title and description in one action', () => {
  const turn = chat.parseAgentReply(JSON.stringify({
    reply: 'ok',
    actions: [{ type: 'add_node', kind: 'storyboard', title: '  清晨海边空镜 ', description: '  清晨海面无人  ' }],
  }), ids);
  assert.deepEqual(turn.actions, [
    { type: 'add_node', kind: 'storyboard', title: '清晨海边空镜', description: '清晨海面无人' },
  ]);
});

test('an add_node without content stays a bare kind', () => {
  const turn = chat.parseAgentReply(JSON.stringify({ reply: 'ok', actions: [{ type: 'add_node', kind: 'text' }] }), ids);
  assert.deepEqual(turn.actions, [{ type: 'add_node', kind: 'text' }]);
});
