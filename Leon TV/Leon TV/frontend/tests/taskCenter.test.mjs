import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: tc } = await loadModule('lib/taskCenter.ts');

const active = [
  { id: 'a1', tool: 'video', model: 'seedance-2.0', status: 'running', created_at: '2026-11-05T10:00:00', input: { prompt: '镜头缓推 海边' } },
];
const history = [
  { id: 'h1', tool: 't2i', model: 'gpt-image-2-all', created_at: '2026-11-05T09:00:00', input: { prompt: '少女在海边' } },
  { id: 'h2', tool: 'llm', model: 'deepseek-v4-flash', created_at: '2026-11-05T11:00:00', input: {} },
];

test('running tasks come first, then completed by newest', () => {
  const list = tc.mergeTaskEntries(active, history);
  assert.deepEqual(list.map(item => item.id), ['a1', 'h2', 'h1']);
  assert.equal(list[0].state, 'running');
  assert.equal(list[1].state, 'completed');
});

test('the same id is never listed twice', () => {
  const list = tc.mergeTaskEntries(active, [...history, { ...active[0] }]);
  assert.equal(list.filter(item => item.id === 'a1').length, 1);
});

test('summaries are trimmed for the queue row', () => {
  const list = tc.mergeTaskEntries([], [{ id: 'x', tool: 't2i', created_at: '2026-11-05T09:00:00', input: { prompt: 'a'.repeat(200) } }]);
  assert.equal(list[0].summary.length, 61);
  assert.ok(list[0].summary.endsWith('…'));
});

test('records without an id are dropped instead of rendering blank rows', () => {
  const list = tc.mergeTaskEntries([{ tool: 'video' }], [{ id: 'ok', tool: 't2i', created_at: '2026-11-05T09:00:00' }]);
  assert.deepEqual(list.map(item => item.id), ['ok']);
});

test('the list is capped', () => {
  const many = Array.from({ length: 50 }, (_, index) => ({
    id: `h${index}`, tool: 't2i', created_at: `2026-11-05T09:${String(index).padStart(2, '0')}:00`,
  }));
  assert.equal(tc.mergeTaskEntries([], many, 24).length, 24);
});

test('counts separate running from completed', () => {
  assert.deepEqual(tc.taskCounts(tc.mergeTaskEntries(active, history)), { running: 1, completed: 2 });
});
