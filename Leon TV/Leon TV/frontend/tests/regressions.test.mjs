import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import ts from 'typescript';

// Exercise the actual TypeScript helpers with the project's existing compiler.
// No browser, model credentials, backend writes or additional dependencies.
const modules = new Map();
async function loadModule(relative) {
  const filename = resolve(relative);
  if (modules.has(filename)) return modules.get(filename);
  let { outputText } = ts.transpileModule(await readFile(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  const imports = [...outputText.matchAll(/from ['"]((?:@\/|\.\/|\.\.\/)[^'"]+)['"]/g)];
  for (const match of imports) {
    const dependency = match[1].startsWith('@/') ? resolve(match[1].slice(2)) : resolve(dirname(filename), match[1]);
    const { url } = await loadModule(`${dependency}.ts`);
    outputText = outputText.replace(match[0], `from '${url}'`);
  }
  const url = `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`;
  const result = { url, module: await import(url) };
  modules.set(filename, result);
  return result;
}
const { module: studio } = await loadModule('lib/studioApi.ts');
const { module: workflow } = await loadModule('lib/workflowApi.ts');
const { module: assets } = await loadModule('components/stages/utils.ts');

test('a successful HTTP status with invalid JSON is an error, not an empty project list', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('<html>proxy failure</html>'));
  await assert.rejects(studio.listStudioProjects(), /JSON|响应/);
});

test('structured backend error messages reach the UI as readable text', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ error: { code: 'UNAVAILABLE', message: '服务暂不可用' } }, { status: 503 }));
  await assert.rejects(studio.listStudioProjects(), /服务暂不可用/);
});

test('failed sandbox task queries must not masquerade as an empty task list', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('unavailable', { status: 503 }));
  await assert.rejects(workflow.fetchSandboxTasks());
});

test('stage streams release the reader after completion', async () => {
  const response = new Response('{"type":"stage_complete"}\n');
  const events = [];
  for await (const event of workflow.parseStreamEvents(response)) events.push(event.type);
  assert.deepEqual(events, ['stage_complete']);
  assert.equal(response.body.locked, false);
});

test('leaving a stage stream early releases its reader', async () => {
  const response = new Response('{"type":"progress","percent":5}\n{"type":"stage_complete"}\n');
  for await (const event of workflow.parseStreamEvents(response)) { assert.equal(event.type, 'progress'); break; }
  assert.equal(response.body.locked, false);
});

test('relative result paths resolve to the mounted backend media route in every view', () => {
  for (const path of ['result/image/a.png', 'code/result/image/a.png', '/code/result/image/a.png']) {
    assert.equal(assets.assetUrl(path), '/code/result/image/a.png');
    assert.equal(studio.mediaUrl(path), '/code/result/image/a.png');
  }
});

test('fragmented NDJSON keeps Unicode, skips heartbeat and detects premature EOF', async () => {
  const bytes = new TextEncoder().encode('{"type":"heartbeat"}\n{"type":"content","content":"你好"}\r\n{"type":"stage_complete"}');
  const response = new Response(new ReadableStream({ start(controller) { for (let i=0;i<bytes.length;i+=2) controller.enqueue(bytes.slice(i,i+2)); controller.close(); } }));
  const events=[];
  for await (const event of workflow.parseStreamEvents(response)) events.push(event);
  assert.deepEqual(events.map(event => event.type), ['content','stage_complete']);
  assert.equal(events[0].content, '你好');
  await assert.rejects(async () => { for await (const event of workflow.parseStreamEvents(new Response('{"type":"progress"}\n'))) void event; }, /提前结束/);
});

const { module: state } = await loadModule('lib/workflowState.ts');
test('pending or partially completed workflows are not labelled completed', () => {
  assert.equal(state.workflowStatus(['pending','pending']), 'pending');
  assert.equal(state.workflowStatus(['completed','pending']), 'pending');
  assert.equal(state.workflowStatus(['completed','completed']), 'completed');
  assert.equal(state.workflowStatus(['waiting','completed']), 'waiting');
  assert.equal(state.stageStatus('session_completed'), 'completed');
  assert.equal(state.stageStatus('stopped'), 'stopped');
  assert.equal(state.stageStatus('future_unknown_state'), 'pending');
});

test('simultaneous catalog consumers share one in-flight request but later requests refresh', async t => {
  let calls = 0;
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  t.mock.method(globalThis, 'fetch', async () => { calls++; await gate; return Response.json({models:[{id:'test-model',label:'测试模型',provider:'test'}]}); });
  const first = workflow.fetchApiModels({modelType:'llm'});
  const second = workflow.fetchApiModels({modelType:'llm'});
  assert.equal(calls, 1);
  release();
  assert.deepEqual(await first, await second);
  await workflow.fetchApiModels({modelType:'llm'});
  assert.equal(calls, 2);
});

test('stage streaming preserves the existing backend address and abort signal', async t => {
  const controller = new AbortController();
  let request;
  t.mock.method(globalThis, 'fetch', async (url, options) => { request={url, options}; return new Response('{"type":"stage_complete"}\n'); });
  await workflow.executeStage('test-session','script_generation',{},controller.signal);
  assert.equal(request.url, 'http://127.0.0.1:8000/api/project/test-session/execute/script_generation');
  assert.equal(request.options.signal, controller.signal);
});

test('external media and local previews keep their URLs', () => {
  for (const path of ['https://example.test/media.mp4','blob:test-image','data:image/png;base64,AAAA','/ui/inspiration-cat.png']) {
    assert.equal(assets.assetUrl(path), path);
    assert.equal(studio.mediaUrl(path), path);
  }
  assert.equal(studio.mediaUrl('/tmp/backend/code/result/image/a.png'), '/code/result/image/a.png');
});

const { module: nextConfig } = await loadModule('next.config.ts');
test('one API rewrite covers existing business routes and health without changing the backend', async () => {
  const rewrites = await nextConfig.default.rewrites();
  assert.deepEqual(rewrites, [
    {source:'/code/:path*',destination:'http://127.0.0.1:8000/code/:path*'},
    {source:'/api/:path*',destination:'http://127.0.0.1:8000/api/:path*'},
  ]);
});

test('a rejected workflow transition must not be reported as confirmed just because HTTP is 200', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({status:'error',message:'当前状态不允许继续'}));
  await assert.rejects(workflow.continueWorkflow('test-session'), /不允许继续/);
});

const { module: registry } = await loadModule('lib/modelRegistry.ts');
test('the two exact Tudou IDs use Tudou credentials without changing model IDs or other providers', () => {
  const groups = registry.groupModelOptions([
    { id: 'minimax-h3' },
    { id: 'seedance-2.5', provider: 'ark' },
    { id: 'seedance-1-0-pro', provider: 'ark' },
  ]);
  assert.deepEqual(groups.find(group => group.provider === 'tudou')?.models.map(model => model.id), ['minimax-h3', 'seedance-2.5']);
  assert.deepEqual(groups.find(group => group.provider === 'ark')?.models.map(model => model.id), ['seedance-1-0-pro']);
});

test('frontend provider mapping does not invent models absent from the backend catalog', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ models: [{ id: 'existing-video', provider: 'ark' }] }));
  const groups = await registry.fetchModelGroupsByType('video');
  assert.deepEqual(groups.flatMap(group => group.models.map(model => model.id)), ['existing-video']);
});

// Compile the actual canvas click handler in a controlled closure. This exercises
// config -> node -> POST without a DOM, a live backend, or paid generation.
const canvasSource = ts.createSourceFile('StudioCanvas.tsx', await readFile('components/studio/StudioCanvas.tsx', 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let runNodeInitializer;
function findRunNode(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(canvasSource) === 'runNode') runNodeInitializer = node.initializer;
  ts.forEachChild(node, findRunNode);
}
findRunNode(canvasSource);
assert.ok(runNodeInitializer, 'the actual canvas runNode handler must be present');
const canvasClosureKeys = ['nodeRunLocks', 'setNotice', 'edges', 'nodes', 'defaultModels', 'modelGroups', 'updateNode', 'queueSave', 'saveChainRef', 'editSeqRef', 'savedSeqRef', 'latestRef'];
const canvasHarness = ts.transpileModule(`export default function createHandler({${canvasClosureKeys.join(',')}}) { return ${runNodeInitializer.getText(canvasSource)}; }`, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const { default: createCanvasHandler } = await import(`data:text/javascript;base64,${Buffer.from(canvasHarness).toString('base64')}`);

async function submitCanvasNode(t, data = {}, generation = {}) {
  const node = { id: 'controlled-video-node', data: { kind: 'video', description: 'controlled test prompt', model: 'minimax-h3', ...data } };
  const requests = [];
  const notices = [];
  const updates = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url === '/api/config') return Response.json({ config: { generation, api_providers: { tudou: { api_key: 'test-only' } } } });
    assert.equal(url, '/api/sandbox/jobs');
    requests.push({ url, options, body: JSON.parse(options.body) });
    return Response.json({ id: requests.at(-1).body.job_id, status: 'running' }, { status: 202 });
  });
  const handler = createCanvasHandler({
    nodeRunLocks: { current: new Set() }, setNotice: message => notices.push(message), edges: [], nodes: [node],
    defaultModels: { video: '' }, modelGroups: { video: [{ provider: 'tudou', models: [{ id: node.data.model }] }] },
    updateNode: (_id, patch) => updates.push(patch), queueSave: () => {}, saveChainRef: { current: Promise.resolve() },
    editSeqRef: { current: 0 }, savedSeqRef: { current: 0 }, latestRef: { current: { nodes: [node] } },
  });
  await handler(node);
  return { requests, notices, updates };
}

test('canvas submits each exact Tudou ID with node 9:16 and 15 seconds over the old default', async t => {
  for (const model of ['minimax-h3', 'seedance-2.5']) {
    const { requests } = await submitCanvasNode(t, { model, ratio: '9:16', duration: 15 }, { video_ratio: '16:9' });
    assert.equal(requests.length, 1);
    assert.equal(requests[0].options.method, 'POST');
    assert.equal(requests[0].body.model, model);
    assert.equal(requests[0].body.ratio, '9:16');
    assert.equal(requests[0].body.duration, 15);
    assert.match(requests[0].body.job_id, /^[a-f0-9]{32}$/);
  }
});

test('canvas respects saved video_ratio, then user settings, then the legacy default', async t => {
  for (const [data, generation, expected] of [
    [{ video_ratio: '9:16' }, { video_ratio: '16:9' }, '9:16'],
    [{}, { video_ratio: '9:16' }, '9:16'],
    [{}, {}, '16:9'],
  ]) {
    const { requests } = await submitCanvasNode(t, data, generation);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].body.ratio, expected);
    assert.equal(Object.hasOwn(requests[0].body, 'duration'), false, 'missing duration keeps the backend default');
  }
});

test('canvas rejects unsupported duration before submitting instead of silently rounding', async t => {
  for (const duration of [2.2, 0, 121]) {
    const { requests, updates } = await submitCanvasNode(t, { duration });
    assert.equal(requests.length, 0);
    assert.match(updates.at(-1)?.error || '', /时长/);
  }
});

const { module: preferences } = await loadModule('lib/appPreferences.ts');
test('LeonTV preferences migrate legacy state without deleting it or overriding newer choices', () => {
  const values = new Map([
    ['xiaoyunque.sidebar-open', 'false'],
    ['xiaoyunque.dismissed-completed-tasks', '["old-completed-task"]'],
  ]);
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  assert.equal(preferences.readAppPreference(storage, 'sidebar'), 'false');
  preferences.migrateAppPreferences(storage);
  assert.equal(values.get('leontv.sidebar-open'), 'false');
  assert.equal(values.get('leontv.dismissed-completed-tasks'), '["old-completed-task"]');
  assert.equal(values.get('xiaoyunque.sidebar-open'), 'false');
  values.set('leontv.sidebar-open', 'true');
  preferences.migrateAppPreferences(storage);
  assert.equal(preferences.readAppPreference(storage, 'sidebar'), 'true');
});

test('legacy preferences remain readable when migration writes are denied', () => {
  const storage = { getItem: key => key === 'xiaoyunque.sidebar-open' ? 'false' : null, setItem: () => { throw new Error('read only'); } };
  assert.doesNotThrow(() => preferences.migrateAppPreferences(storage));
  assert.equal(preferences.readAppPreference(storage, 'sidebar'), 'false');
});
