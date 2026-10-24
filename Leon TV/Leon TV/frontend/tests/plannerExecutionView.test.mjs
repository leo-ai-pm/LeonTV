import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadModule } from './helpers/loadTypescript.mjs';

const { module: view } = await loadModule('lib/plannerExecutionView.ts');

test('an existing live execution cannot be relabelled synthetic by the current policy', () => {
  assert.equal(view.observedExecutionEnvironment({ environment: 'live' }, { environment: 'synthetic_test' }), 'live');
  assert.equal(view.observedExecutionEnvironment({ environment: 'live' }, { environment: 'disabled' }), 'live');
});

test('an existing synthetic or disabled execution keeps its observed environment', () => {
  assert.equal(view.observedExecutionEnvironment({ environment: 'synthetic_test' }, { environment: 'live' }), 'synthetic_test');
  assert.equal(view.observedExecutionEnvironment({ environment: 'disabled' }, { environment: 'live' }), 'disabled');
});

test('an unavailable execution observation cannot claim zero live calls from a current synthetic policy', () => {
  assert.equal(view.observedExecutionEnvironment(null, { environment: 'synthetic_test' }), null);
  assert.equal(view.observedExecutionEnvironment(null, null), null);
});
