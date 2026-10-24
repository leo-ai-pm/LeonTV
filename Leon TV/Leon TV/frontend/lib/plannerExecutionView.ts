import type { ExecutionDetail } from './plannerExecutionTypes';

/** Persisted observations keep their own environment when the current policy changes. */
export function observedExecutionEnvironment(execution: Pick<ExecutionDetail, 'environment'> | null): string | null {
  return execution?.environment ?? null;
}

export function isSyntheticExecutionEnvironment(environment: string | null): boolean {
  return environment === 'synthetic_test' || environment === 'synthetic_empirical_test';
}
