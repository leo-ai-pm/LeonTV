import { readFile } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import ts from 'typescript';

const modules = new Map();
export async function loadModule(relative) {
  const filename = resolve(relative);
  if (modules.has(filename)) return modules.get(filename);
  let { outputText } = ts.transpileModule(await readFile(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  for (const match of [...outputText.matchAll(/from ['"]((?:@\/|\.\/|\.\.\/)[^'"]+)['"]/g)]) {
    const dependency = match[1].startsWith('@/') ? resolve(match[1].slice(2)) : resolve(dirname(filename), match[1]);
    const { url } = await loadModule(`${dependency}.ts`);
    outputText = outputText.replace(match[0], `from '${url}'`);
  }
  const url = `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`;
  const result = { url, module: await import(url) };
  modules.set(filename, result);
  return result;
}
