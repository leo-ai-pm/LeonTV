import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: api } = await loadModule('lib/creatorBriefApi.ts');
const { module: control } = await loadModule('lib/creatorBriefController.ts');
const { module: entry } = await loadModule('lib/entryRouting.ts');
const A = 'a'.repeat(32), B = 'b'.repeat(32), HASH = 'c'.repeat(64);
const production = { duration_seconds: 15, ratio: '9:16', style: 'realistic_cinematic' };
const SYNTHETIC = '  合成测试剧本 ONLY，保留首尾与换行。\n第二行。  ';
function doc(overrides = {}) {
  const revision = overrides.revision ?? 1, script = overrides.script ?? SYNTHETIC, budget = overrides.budget_cny ?? null;
  return {
    id: A, revision, title: '合成测试简报', production, budget_cny: budget, currency: 'CNY', budget_status: budget === null ? 'pending' : 'draft',
    authorization_status: 'not_authorized', execution_enabled: false, source_hash: HASH, source_version: revision,
    script_characters: Array.from(script).length, created_at: '2026-10-20T00:00:00Z', updated_at: `2026-10-20T00:00:${String(revision).padStart(2,'0')}Z`,
    script, requirements: '合成要求 ONLY', client_request_key: 'original-create-key', current_revision: revision, is_current: true,
    versions: Array.from({ length: revision }, (_, i) => ({ revision: i + 1, source_version: i + 1, source_hash: HASH, saved_at: '2026-10-20T00:00:00Z' })), ...overrides,
  };
}
function summary(value) { const { script, requirements, client_request_key, current_revision, is_current, versions, ...safe } = value; void [script, requirements, client_request_key, current_revision, is_current, versions]; return safe; }
function fromBody(body, overrides = {}) { return doc({ ...body, client_request_key: body.client_request_key || 'original-create-key', ...overrides }); }
function result(value, applied = value.revision, replayed = false) { return Response.json({ brief: value, applied_revision: applied, replayed }); }
function list(values = []) { return Response.json({ briefs: values.map(summary), next_offset: null, has_more: false }); }
function failure(status, code) { return Response.json({ detail: { code, message: 'Synthetic failure; no body', ...(code === 'REVISION_CONFLICT' ? { current_revision: 2 } : {}) } }, { status }); }
function store() {
  const values = new Map(), writes = [];
  return { values, writes, getItem: key => values.get(key) ?? null, setItem: (key, value) => { writes.push(value); values.set(key, value); }, removeItem: key => values.delete(key) };
}
function client(storage = store()) { return new control.CreatorBriefController(() => storage); }
function edit(controller, patch = {}) { controller.edit({ title: '合成测试简报', script: SYNTHETIC, requirements: '合成要求 ONLY', budgetText: '', ...patch }); }

test('default Creator routing preserves six-stage session/stage deep links', () => {
  assert.equal(entry.isLegacyWorkflowQuery({}), false);
  assert.equal(entry.isLegacyWorkflowQuery({ session: 'saved-session', stage: 'storyboard' }), true);
  assert.equal(entry.isLegacyWorkflowQuery({ stage: 'storyboard' }), true);
  assert.equal(entry.isLegacyWorkflowQuery({ legacy: 'workflow' }), true);
});

test('budget pending is null, explicit zero stays a decimal draft, and unsupported amounts are rejected', () => {
  assert.equal(control.normalizeCreatorBudget(''), null);
  assert.equal(control.normalizeCreatorBudget('0'), '0.00');
  assert.equal(control.normalizeCreatorBudget('00120.5'), '120.50');
  assert.equal(control.normalizeCreatorBudget('1000000.00'), '1000000.00');
  for (const value of ['-1','1e2','0.001','1000000.01','Infinity']) assert.throws(() => control.normalizeCreatorBudget(value));
});

test('real controller persists only a request key/digest before POST and preserves exact script text', async t => {
  const storage = store(), c = client(storage); edit(c);
  let posted;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/api/creator/briefs'); assert.equal(options.method, 'POST');
    posted = JSON.parse(options.body);
    const intent = JSON.parse(storage.getItem(c.storageKey));
    assert.equal(intent.key, posted.client_request_key); assert.match(intent.content_hash, /^[a-f0-9]{64}$/);
    assert.equal(JSON.stringify(intent).includes(SYNTHETIC), false);
    assert.equal(Object.hasOwn(intent, 'script'), false); assert.equal(Object.hasOwn(intent, 'requirements'), false); assert.equal(Object.hasOwn(intent, 'title'), false); assert.equal(Object.hasOwn(intent, 'budget_cny'), false);
    return result(fromBody(posted));
  });
  const saved = await c.save();
  assert.equal(posted.script, SYNTHETIC); assert.equal(posted.budget_cny, null); assert.deepEqual(posted.production, production);
  assert.equal(saved.execution_enabled, false); assert.equal(saved.authorization_status, 'not_authorized');
  assert.equal(c.getSnapshot().dirty, false); assert.equal(storage.getItem(c.storageKey), null);
});

test('safe list and by-key calls never fetch full text implicitly or retain extra response body fields', async t => {
  const urls = [];
  t.mock.method(globalThis, 'fetch', async url => { urls.push(url); return url.includes('/by-key/') ? Response.json({ brief: doc() }) : Response.json({ briefs: [doc()], next_offset: null, has_more: false }); });
  const c = client(); await c.loadList();
  const found = await api.creatorBriefApi.byKey('test-create-key');
  assert.equal(urls.length, 2); assert.ok(urls[0].includes('?offset=0&limit=50'));
  for (const value of [found, c.getSnapshot().briefs[0]]) {
    assert.equal(Object.hasOwn(value, 'script'), false); assert.equal(Object.hasOwn(value, 'requirements'), false);
    assert.equal(Object.hasOwn(value, 'client_request_key'), false);
  }
});

test('a lost POST response restores through original by-key summary then explicit ID after remount without another POST', async t => {
  const storage = store(); let posted, saved, posts = 0; const urls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    urls.push(url);
    if (options.method === 'POST') { posts++; posted = JSON.parse(options.body); saved = fromBody(posted); throw new TypeError('response lost'); }
    if (url.includes('?offset=')) return list([saved]);
    if (url.includes('/by-key/')) { assert.equal(url.split('/').at(-1), posted.client_request_key); return Response.json({ brief: summary(saved) }); }
    assert.equal(url, `/api/creator/briefs/${A}`); return Response.json({ brief: saved });
  });
  const first = client(storage); edit(first); await assert.rejects(first.save(), error => error.code === 'OUTCOME_UNKNOWN');
  const restored = client(storage); await restored.initialize(null);
  assert.equal(restored.getSnapshot().document.id, A); assert.equal(restored.getSnapshot().fields.script, SYNTHETIC);
  assert.equal(restored.getSnapshot().dirty, false); assert.equal(posts, 1);
  assert.ok(urls.indexOf(`/api/creator/briefs/by-key/${posted.client_request_key}`) < urls.lastIndexOf(`/api/creator/briefs/${A}`));
});

test('by-key 404, 503 or network loss retains original key and current in-memory edits without replacement creates', async t => {
  for (const status of [404, 503, 'network']) {
    const storage = store(), c = client(storage); edit(c);
    t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('response lost'); });
    await assert.rejects(c.save());
    c.edit({ script: '继续编辑的合成稿，不能丢失。' });
    const before = storage.getItem(c.storageKey);
    t.mock.method(globalThis, 'fetch', async (_url, options) => { assert.equal(options.method, 'GET'); if (status === 'network') throw new TypeError('network unavailable'); return failure(status, status === 404 ? 'NOT_FOUND' : 'PRIVATE_STORAGE_UNAVAILABLE'); });
    await c.recover(false);
    assert.equal(storage.getItem(c.storageKey), before); assert.equal(c.getSnapshot().fields.script, '继续编辑的合成稿，不能丢失。');
    await assert.rejects(c.save(), /尚未确认/);
  }
});

test('explicit creation retry queries first and sends original body while preserving later local edits', async t => {
  const storage = store(), c = client(storage); edit(c); let original;
  t.mock.method(globalThis, 'fetch', async (_url, options) => { original = JSON.parse(options.body); throw new TypeError('lost'); });
  await assert.rejects(c.save()); c.edit({ script: '比已提交原稿更新的本地修改' });
  const methods = [];
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    methods.push(options.method);
    if (options.method === 'GET') return failure(404, 'NOT_FOUND');
    assert.deepEqual(JSON.parse(options.body), original); return result(fromBody(original));
  });
  await c.recover(true);
  assert.deepEqual(methods, ['GET','POST']); assert.equal(c.getSnapshot().fields.script, '比已提交原稿更新的本地修改');
  assert.equal(c.getSnapshot().dirty, true); assert.equal(c.getSnapshot().document.revision, 1);
});

test('edits made while POST is pending survive a successful slow response, including a partially typed budget', async t => {
  const c = client(); edit(c); let release, posted;
  const gate = new Promise(resolve => { release = resolve; });
  t.mock.method(globalThis, 'fetch', async (_url, options) => { posted = JSON.parse(options.body); await gate; return result(fromBody(posted)); });
  const saving = c.save();
  while (!posted) await new Promise(resolve => setImmediate(resolve));
  c.edit({ script: '保存中继续输入的合成稿', budgetText: '1.' }); release(); await saving;
  assert.equal(c.getSnapshot().fields.script, '保存中继续输入的合成稿'); assert.equal(c.getSnapshot().fields.budgetText, '1.');
  assert.equal(c.getSnapshot().dirty, true); assert.equal(c.getSnapshot().document.revision, 1);
});

test('revision conflict preserves local text, does not retry, and requires explicit rebase before a fresh-key save', async t => {
  const c = client(); const base = doc(), latest = doc({ revision: 2, script: '服务器合成新稿' });
  let puts = [], conflict = true;
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    if (options.method === 'GET') return Response.json({ brief: puts.length ? latest : base });
    const body = JSON.parse(options.body); puts.push(body);
    return conflict ? failure(409, 'REVISION_CONFLICT') : result(fromBody(body, { revision: 3 }), 3);
  });
  await c.open(A); c.edit({ script: '本地合成修改，冲突时须保留' });
  await assert.rejects(c.save(), error => error.code === 'REVISION_CONFLICT');
  assert.equal(puts.length, 1); assert.equal(c.getSnapshot().fields.script, '本地合成修改，冲突时须保留');
  assert.equal(c.getSnapshot().conflict.revision, 2); assert.equal(c.getSnapshot().document.revision, 1);
  await assert.rejects(c.save(), /核对当前版本/); assert.equal(puts.length, 1);
  c.rebase(true); assert.equal(c.getSnapshot().document.revision, 2); assert.equal(c.getSnapshot().dirty, true);
  conflict = false; await c.save();
  assert.equal(puts[1].expected_revision, 2); assert.notEqual(puts[0].client_update_key, puts[1].client_update_key);
});

test('a conflict remains blocking even when the follow-up latest-version read fails', async t => {
  const c = client(); let saving = false;
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    if (options.method === 'PUT') { saving = true; return failure(409,'REVISION_CONFLICT'); }
    return saving ? failure(503,'PRIVATE_STORAGE_UNAVAILABLE') : Response.json({ brief: doc() });
  });
  await c.open(A); c.edit({ script: '合成修改' }); await assert.rejects(c.save());
  assert.equal(c.getSnapshot().conflictDetected, true); assert.equal(c.getSnapshot().conflict, null);
  await assert.rejects(c.save(), /核对当前版本/); assert.equal(c.getSnapshot().fields.script, '合成修改');
});

test('lost update survives remount without browser-stored text and replays exact original key/revision', async t => {
  const storage = store(), first = client(storage); let original, saved = doc(), lose = true;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url.includes('?offset=')) return list([saved]);
    if (options.method === 'GET') return Response.json({ brief: saved });
    const body = JSON.parse(options.body);
    if (lose) { original = body; saved = fromBody(body,{ revision: 2 }); throw new TypeError('update response lost'); }
    assert.deepEqual(body, original); return result(saved,2,true);
  });
  await first.open(A); first.edit({ script: '新的合成剧本，更新超时恢复用' }); await assert.rejects(first.save());
  const raw = storage.getItem(first.storageKey); assert.equal(raw.includes('新的合成剧本'),false);
  const restored = client(storage); await restored.initialize(A);
  assert.equal(restored.getSnapshot().pending.key, original.client_update_key); assert.equal(restored.getSnapshot().document.revision,2);
  lose = false; await restored.recover(true);
  assert.equal(restored.getSnapshot().pending,null); assert.equal(restored.getSnapshot().document.revision,2);
});

test('when update replay returns a newer current version, the controller preserves local input for explicit comparison', async t => {
  const storage = store(), c = client(storage); let original, lose = true;
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    if (options.method === 'GET') return Response.json({ brief: doc() });
    const body = JSON.parse(options.body);
    if (lose) { original = body; throw new TypeError('lost'); }
    assert.deepEqual(body,original); return result(doc({ revision:3,script:'另一个窗口保存的服务器 v3' }),2,true);
  });
  await c.open(A); c.edit({script:'原保存的合成 v2'}); await assert.rejects(c.save());
  c.edit({script:'当前未保存的合成 v4 修改'}); lose=false; await c.recover(true);
  assert.equal(c.getSnapshot().fields.script,'当前未保存的合成 v4 修改'); assert.equal(c.getSnapshot().conflictDetected,true);
  assert.equal(c.getSnapshot().conflict.revision,3);
});

test('a remounted pending create cannot POST different re-entered content under the old key', async t => {
  const storage=store(),first=client(storage);edit(first);
  t.mock.method(globalThis,'fetch',async()=>{throw new TypeError('lost');});await assert.rejects(first.save());
  const before=storage.getItem(first.storageKey),second=client(storage);second.edit({script:'不同的新输入'});let posts=0;
  t.mock.method(globalThis,'fetch',async(_url,options)=>{if(options.method==='POST')posts++;return failure(404,'NOT_FOUND');});
  await assert.rejects(second.recover(true),/原请求不一致/);assert.equal(posts,0);assert.equal(storage.getItem(first.storageKey),before);
});

test('double clicks and independent controllers cannot dispatch duplicate create intents', async t => {
  const storage=store(),one=client(storage),two=client(storage);edit(one);edit(two);let posts=0;
  t.mock.method(globalThis,'fetch',async(_url,options)=>{posts++;return result(fromBody(JSON.parse(options.body)));});
  const pending=one.save();await assert.rejects(one.save(),/勿重复/);
  const results=await Promise.allSettled([pending,two.save()]);
  assert.equal(results.filter(result=>result.status==='fulfilled').length,1);assert.equal(posts,1);
});

test('unwritable request-key storage prevents dispatch and keeps the draft editable', async t => {
  const c=client({getItem:()=>null,setItem:()=>{throw new Error('quota');},removeItem:()=>{}});edit(c);let calls=0;
  t.mock.method(globalThis,'fetch',async()=>{calls++;throw new Error('must not dispatch');});
  await assert.rejects(c.save(),/持久保存原请求键/);assert.equal(calls,0);assert.equal(c.getSnapshot().fields.script,SYNTHETIC);assert.equal(c.getSnapshot().saving,false);
});

test('old slow reads and old save responses cannot replace the selected brief', async t => {
  const c=client();let releaseA;const gate=new Promise(resolve=>{releaseA=resolve;});
  t.mock.method(globalThis,'fetch',async url=>{if(url.endsWith(A)){await gate;return Response.json({brief:doc()});}return Response.json({brief:doc({id:B,title:'简报 B',script:'合成 B'})});});
  const old=c.open(A);await c.open(B);releaseA();await old;assert.equal(c.getSnapshot().document.id,B);assert.equal(c.getSnapshot().fields.script,'合成 B');
  let releaseWrite,posted;const writeGate=new Promise(resolve=>{releaseWrite=resolve;});
  t.mock.method(globalThis,'fetch',async(url,options)=>{if(options.method==='POST'){posted=JSON.parse(options.body);await writeGate;return result(fromBody(posted));}return Response.json({brief:doc({id:B,title:'简报 B',script:'合成 B'})});});
  c.newDraft();edit(c);const saving=c.save();while(!posted)await new Promise(resolve=>setImmediate(resolve));await c.open(B);releaseWrite();await saving;
  assert.equal(c.getSnapshot().document.id,B);assert.equal(c.getSnapshot().fields.script,'合成 B');
});

test('a slower refresh cannot roll back a newer server revision or overwrite unsaved edits', async t => {
  const c=client();t.mock.method(globalThis,'fetch',async()=>Response.json({brief:doc()}));await c.open(A);
  let release;const gate=new Promise(resolve=>{release=resolve;});let calls=0;
  t.mock.method(globalThis,'fetch',async()=>{calls++;if(calls===1){await gate;return Response.json({brief:doc({revision:2,script:'旧响应'})});}return Response.json({brief:doc({revision:3,script:'较新响应'})});});
  const old=c.refresh();await c.refresh();release();await old;assert.equal(c.getSnapshot().document.revision,3);assert.equal(c.getSnapshot().fields.script,'较新响应');
  c.edit({script:'保留未保存输入'});await c.refresh();assert.equal(c.getSnapshot().fields.script,'保留未保存输入');
});

test('the real API adapter aborts a never-returning fetch at its deadline and always clears its timer', async t => {
  let deadline,cleared=0;
  t.mock.method(globalThis,'setTimeout',(callback,ms)=>{assert.equal(ms,15000);deadline=callback;return 123;});
  t.mock.method(globalThis,'clearTimeout',()=>{cleared++;});
  t.mock.method(globalThis,'fetch',async(_url,options)=>new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true})));
  const pending=api.creatorBriefApi.create({client_request_key:'bounded-timeout-key',title:'合成',script:SYNTHETIC,requirements:'',production,budget_cny:null});
  assert.equal(typeof deadline,'function');deadline();
  await assert.rejects(pending,error=>error instanceof api.CreatorBriefError&&error.code==='OUTCOME_UNKNOWN');assert.equal(cleared,1);
});

test('a write timeout releases the controller lock while preserving original intent and input', async t => {
  const storage=store(),c=client(storage);edit(c);
  t.mock.method(globalThis,'setTimeout',callback=>{queueMicrotask(callback);return 123;});t.mock.method(globalThis,'clearTimeout',()=>{});
  t.mock.method(globalThis,'fetch',async(_url,options)=>new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true})));
  await assert.rejects(c.save(),error=>error.code==='OUTCOME_UNKNOWN');assert.equal(c.getSnapshot().saving,false);assert.ok(c.getSnapshot().pending);assert.equal(c.getSnapshot().fields.script,SYNTHETIC);
  const key=c.getSnapshot().pending.key;
  t.mock.method(globalThis,'fetch',async()=>failure(404,'NOT_FOUND'));await c.recover(false);assert.equal(c.getSnapshot().pending.key,key);
});

test('unsafe authorization/execution responses are rejected and invalid forms send no request', async t => {
  t.mock.method(globalThis,'fetch',async()=>Response.json({brief:doc({authorization_status:'authorized',execution_enabled:true})}));
  await assert.rejects(api.creatorBriefApi.get(A),/D07/);
  const c=client();edit(c,{script:' ',budgetText:'1e2'});let calls=0;t.mock.method(globalThis,'fetch',async()=>{calls++;throw new Error('must not dispatch');});
  assert.equal(await c.save(),null);assert.equal(calls,0);assert.ok(c.getSnapshot().fieldErrors.script);assert.ok(c.getSnapshot().fieldErrors.budgetText);
});

test('ordinary offline fetch errors use Chinese unknown-outcome text and retain the same key and draft', async t => {
  const storage = store(), c = client(storage); edit(c);
  t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('Failed to fetch'); });
  await assert.rejects(c.save(), error => error instanceof api.CreatorBriefError && error.code === 'OUTCOME_UNKNOWN' && /网络连接中断，保存结果待确认/.test(error.message));
  const before = storage.getItem(c.storageKey);
  assert.ok(before); assert.equal(c.getSnapshot().saving, false); assert.equal(c.getSnapshot().fields.script, SYNTHETIC);
  await c.recover(false);
  assert.equal(storage.getItem(c.storageKey), before); assert.equal(c.getSnapshot().fields.script, SYNTHETIC);
  assert.match(c.getSnapshot().error, /网络连接中断.*重查原记录/);
  assert.equal(c.getSnapshot().error.includes('Failed to fetch'), false);
});
