import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: imp } = await loadModule('lib/studioImport.ts');

const ABS = '/Users/Admin/Documents/proj/backend/code/result/uploads/0123456789abcdef0123456789abcdef_beach.png';

test('image and video extensions map to canvas node kinds', () => {
  assert.equal(imp.importKindForName('frame.png'), 'image');
  assert.equal(imp.importKindForName('FRAME.PNG'), 'image');
  assert.equal(imp.importKindForName('photo.jpeg'), 'image');
  assert.equal(imp.importKindForName('clip.mp4'), 'video');
  assert.equal(imp.importKindForName('clip.MOV'), 'video');
  assert.equal(imp.importKindForName('clip.webm'), 'video');
});

test('unsupported or extensionless files are rejected', () => {
  assert.equal(imp.importKindForName('notes.txt'), null);
  assert.equal(imp.importKindForName('archive.zip'), null);
  assert.equal(imp.importKindForName('README'), null);
  assert.equal(imp.importKindForName(''), null);
});

test('an absolute upload path becomes a canvas-relative result path', () => {
  assert.equal(imp.toCanvasMediaPath(ABS), 'result/uploads/0123456789abcdef0123456789abcdef_beach.png');
});

test('paths outside result/ are refused instead of guessed', () => {
  assert.equal(imp.toCanvasMediaPath('/tmp/uploads/frame.png'), null);
  assert.equal(imp.toCanvasMediaPath('/Users/x/backend/code/temp/x.png'), null);
  assert.equal(imp.toCanvasMediaPath(''), null);
});

test('the stored title drops the upload hash prefix and the extension', () => {
  const result = imp.describeImportedMedia('0123456789abcdef0123456789abcdef_海边 首帧.png', ABS);
  assert.equal(result.kind, 'image');
  assert.equal(result.title, '海边 首帧');
  assert.equal(result.mediaPath, 'result/uploads/0123456789abcdef0123456789abcdef_beach.png');
});

test('a rejected file yields null so the caller never writes a broken node', () => {
  assert.equal(imp.describeImportedMedia('notes.txt', ABS), null);
  assert.equal(imp.describeImportedMedia('frame.png', '/tmp/outside/frame.png'), null);
});
