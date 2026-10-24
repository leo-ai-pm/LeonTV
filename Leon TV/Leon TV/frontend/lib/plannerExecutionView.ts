import type { ExecutionDetail } from './plannerExecutionTypes';

/** Persisted observations keep their own environment when the current policy changes. */
export function observedExecutionEnvironment(execution: Pick<ExecutionDetail, 'environment'> | null): string | null {
  return execution?.environment ?? null;
}
