import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: mod } = await loadModule('lib/canvasBus.ts');

function spyHandlers() {
  const calls = [];
  return {
    calls,
    handlers: {
      addNode: kind => calls.push(`add:${kind}`),
      updateNode: (id, patch) => calls.push(`update:${id}:${JSON.stringify(patch)}`),
      deleteNode: id => calls.push(`delete:${id}`),
      runNode: id => calls.push(`run:${id}`),
      exportVideo: () => calls.push('export'),
      runPipeline: scope => calls.push(`pipeline:${scope}`),
      snapshot: () => ({ title: 't', nodes: [{ id: 'n1', kind: 'image', title: 'frame' }] }),
    },
  };
}

test('commands are refused until a canvas registers', () => {
  const bus = mod.createCanvasBus();
  assert.equal(bus.dispatch({ type: 'export-video' }), false);
  assert.equal(bus.snapshot(), null);
  assert.equal(bus.getSelection(), null);
});

test('each id-based command reaches its handler', () => {
  const bus = mod.createCanvasBus();
  const { calls, handlers } = spyHandlers();
  bus.register(handlers);
  assert.equal(bus.dispatch({ type: 'add-node', kind: 'video' }), true);
  assert.equal(bus.dispatch({ type: 'update-node', id: 'n1', patch: { title: 'x' } }), true);
  assert.equal(bus.dispatch({ type: 'delete-node', id: 'n1' }), true);
  assert.equal(bus.dispatch({ type: 'run-node', id: 'n1' }), true);
  assert.equal(bus.dispatch({ type: 'export-video' }), true);
  assert.equal(bus.dispatch({ type: 'run-pipeline', scope: 'single' }), true);
  assert.deepEqual(calls, ['add:video', 'update:n1:{"title":"x"}', 'delete:n1', 'run:n1', 'export', 'pipeline:single']);
});

test('the snapshot is only readable while a canvas is registered', () => {
  const bus = mod.createCanvasBus();
  const { handlers } = spyHandlers();
  const release = bus.register(handlers);
  assert.equal(bus.snapshot().nodes[0].id, 'n1');
  release();
  assert.equal(bus.snapshot(), null);
});

test('unregistering stops dispatch and leaves later panels usable', () => {
  const bus = mod.createCanvasBus();
  const first = spyHandlers();
  const release = bus.register(first.handlers);
  release();
  assert.equal(bus.dispatch({ type: 'delete-node', id: 'n1' }), false);
  const second = spyHandlers();
  bus.register(second.handlers);
  assert.equal(bus.dispatch({ type: 'delete-node', id: 'n1' }), true);
  assert.deepEqual(second.calls, ['delete:n1']);
});

test('a stale cleanup does not remove the newer registration', () => {
  const bus = mod.createCanvasBus();
  const first = spyHandlers();
  const releaseFirst = bus.register(first.handlers);
  const second = spyHandlers();
  bus.register(second.handlers);
  releaseFirst();
  assert.equal(bus.dispatch({ type: 'run-node', id: 'n1' }), true);
  assert.deepEqual(second.calls, ['run:n1']);
});

test('selection changes notify subscribers and are idempotent', () => {
  const bus = mod.createCanvasBus();
  let notifications = 0;
  bus.subscribe(() => { notifications += 1; });
  const node = { id: 'a', data: { kind: 'image', title: 't' } };
  bus.setSelection(node);
  bus.setSelection(node);
  assert.equal(notifications, 1);
  assert.equal(bus.getSelection(), node);
  bus.setSelection(null);
  assert.equal(notifications, 2);
});

test('unsubscribed listeners stop receiving updates', () => {
  const bus = mod.createCanvasBus();
  let notifications = 0;
  const off = bus.subscribe(() => { notifications += 1; });
  bus.setSelection({ id: 'a', data: { kind: 'text', title: 't' } });
  off();
  bus.setSelection(null);
  assert.equal(notifications, 1);
});
