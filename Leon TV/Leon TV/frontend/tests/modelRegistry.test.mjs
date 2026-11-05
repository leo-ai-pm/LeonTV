import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: registry } = await loadModule('lib/modelRegistry.ts');

test('verification state is carried through grouping', () => {
  const groups = registry.groupModelOptions([
    { id: 'seedance-2.0', label: 'Seedance 2.0', provider: 'tudou', api_contract_verified: true },
    { id: 'wan3.0', label: 'Wan 3.0', provider: 'tudou', api_contract_verified: false },
  ]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].provider, 'tudou');
  assert.deepEqual(groups[0].models.map(m => [m.id, m.verified]), [['seedance-2.0', true], ['wan3.0', false]]);
});

test('a model without the flag is treated as verified for display', () => {
  const groups = registry.groupModelOptions([{ id: 'x', label: 'X', provider: 'dashscope' }]);
  assert.equal(groups[0].models[0].verified, true);
});

test('tudou relay video ids stay grouped under the relay provider', () => {
  const groups = registry.groupModelOptions([
    { id: 'minimax-h3', label: 'MiniMax H3' },
    { id: 'seedance-2.5', label: 'Seedance 2.5' },
  ]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].provider, 'tudou');
});
