import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { createRequire } from 'node:module';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import { loadModule } from './helpers/loadTypescript.mjs';

// Render the actual panel with real React and production controller/view modules.
// SSR starts in reading=true; no fake JSX factory, DOM or controller bypass.
const filename = resolve('components/creator/PlannerMediaPanel.tsx');
const actualModules = new Map();
for (const name of ['plannerMediaController', 'plannerMediaView']) {
  actualModules.set(`@/lib/${name}`, (await loadModule(`lib/${name}.ts`)).module);
}
const { outputText } = ts.transpileModule(await readFile(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
});
const require = createRequire(filename), compiled = { exports: {} };
new Function('require', 'module', 'exports', outputText)(name => actualModules.has(name) ? actualModules.get(name) : require(name), compiled, compiled.exports);
const PlannerMediaPanel = compiled.exports.default;

test('actual media panel reading state retains a native focusable refresh button and accessible name', () => {
  const markup = renderToStaticMarkup(createElement(PlannerMediaPanel, { task: { id: 'a'.repeat(32), source: { snapshot_hash: 'b'.repeat(64) } } }));
  const button = markup.match(/<button\b[^>]*>[\s\S]*?刷新图片记录<\/button>/)?.[0];
  assert.ok(button);
  assert.match(button, /type="button"/);
  assert.match(button, /aria-disabled="true"/);
  assert.doesNotMatch(button, /\sdisabled(?:=|\s|>)/);
  assert.doesNotMatch(button, /tabindex="-1"|autofocus/i);
  assert.match(markup, /aria-busy="true"/);
  assert.match(markup, /正在读取分镜图片记录/);
});
