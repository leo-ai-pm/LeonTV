import type { StageStatus } from '@/components/stages/types';

export function stageStatus(value: unknown): StageStatus {
  if (value === 'session_completed') return 'completed';
  if (value === 'failed') return 'error';
  return ['pending', 'running', 'waiting', 'completed', 'error', 'stopped'].includes(String(value))
    ? value as StageStatus : 'pending';
}

export function workflowStatus(statuses: StageStatus[]): StageStatus {
  if (statuses.includes('running')) return 'running';
  if (statuses.includes('waiting')) return 'waiting';
  if (statuses.includes('error')) return 'error';
  if (statuses.includes('stopped')) return 'stopped';
  return statuses.length > 0 && statuses.every(status => status === 'completed') ? 'completed' : 'pending';
}
