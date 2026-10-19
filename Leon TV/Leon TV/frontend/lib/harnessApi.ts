import type { HarnessActionRequest, HarnessCreateRequest, HarnessEvents, HarnessReviewRequest, HarnessRun } from '@/lib/harnessTypes';

export class HarnessApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('运行接口返回格式错误。');
  return value as Record<string, unknown>;
}

async function request(url: string, body?: unknown, signal?: AbortSignal): Promise<Record<string, unknown>> {
  const response = await fetch(url, body === undefined ? { signal, cache: 'no-store' } : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  });
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const payload = data && typeof data === 'object' ? data as Record<string, unknown> : {};
    const detail = payload.detail && typeof payload.detail === 'object' ? payload.detail as Record<string, unknown> : {};
    throw new HarnessApiError(response.status, typeof detail.code === 'string' ? detail.code : `HTTP_${response.status}`,
      typeof detail.message === 'string' ? detail.message : typeof payload.detail === 'string' ? payload.detail : `运行接口请求失败（HTTP ${response.status}）。`);
  }
  return object(data);
}

export function readHarnessRun(value: unknown): HarnessRun {
  const run = object(value);
  if (run.schema_version !== 1 || run.mode !== 'test' || run.test_mode !== true || run.evidence_type !== 'fixture' ||
      typeof run.id !== 'string' || typeof run.client_request_key !== 'string' || !Number.isInteger(run.revision) ||
      typeof run.status !== 'string' || !Array.isArray(run.artifacts) || !Array.isArray(run.reviews) ||
      !Array.isArray(run.plan) || !Array.isArray(run.allowed_actions)) throw new Error('运行接口与 D06 v1 测试合同不一致，已停止操作。');
  const budget = object(run.budget);
  const production = object(run.production);
  const source = object(run.source);
  object(run.metrics);
  if (production.ratio !== '9:16' || production.duration_ms !== 15000 || typeof source.hash !== 'string' ||
      !Array.isArray(source.record_ids) || !source.record_ids.every(id => typeof id === 'string') ||
      !Number.isInteger(budget.unresolved_operations)) throw new Error('运行规格或预算字段与 D06 v1 不一致。');
  for (const entry of run.artifacts) {
    const artifact = object(entry);
    if (typeof artifact.id !== 'string' || !Number.isInteger(artifact.version) || artifact.mode !== 'test' ||
      !Array.isArray(artifact.dependencies) || !Array.isArray(artifact.required_scopes) || !Array.isArray(artifact.review_ids) ||
      typeof artifact.stale !== 'boolean') throw new Error('运行产物格式错误，已停止操作。');
  }
  for (const entry of run.reviews) {
    const review = object(entry);
    object(review.scopes);
    if (typeof review.artifact_id !== 'string' || !Number.isInteger(review.artifact_version) || typeof review.valid !== 'boolean') throw new Error('运行审查格式错误，已停止操作。');
  }
  return run as unknown as HarnessRun;
}

const root = '/api/harness/runs';
export const harnessApi = {
  async create(input: HarnessCreateRequest): Promise<HarnessRun> { return readHarnessRun((await request(root, input)).run); },
  async byKey(key: string, signal?: AbortSignal): Promise<HarnessRun> { return readHarnessRun((await request(`${root}/by-key/${encodeURIComponent(key)}`, undefined, signal)).run); },
  async get(id: string, signal?: AbortSignal): Promise<HarnessRun> { return readHarnessRun((await request(`${root}/${encodeURIComponent(id)}`, undefined, signal)).run); },
  async list(projectId: string, signal?: AbortSignal): Promise<HarnessRun[]> {
    const data = await request(`${root}?project_id=${encodeURIComponent(projectId)}`, undefined, signal);
    if (!Array.isArray(data.runs)) throw new Error('运行列表格式错误。');
    return data.runs.map(readHarnessRun);
  },
  async events(id: string, after = 0, signal?: AbortSignal): Promise<HarnessEvents> {
    const data = await request(`${root}/${encodeURIComponent(id)}/events?after_sequence=${after}&limit=100`, undefined, signal);
    if (data.mode !== 'test' || !Array.isArray(data.events) || !Number.isInteger(data.next_sequence) || typeof data.has_more !== 'boolean') throw new Error('运行事件格式错误。');
    for (const value of data.events) {
      const event = object(value);
      if (event.mode !== 'test' || event.run_id !== id || !Number.isInteger(event.sequence) || typeof event.type !== 'string') throw new Error('运行事件标识错误。');
    }
    return data as unknown as HarnessEvents;
  },
  async action(id: string, input: HarnessActionRequest): Promise<HarnessRun> { return readHarnessRun((await request(`${root}/${encodeURIComponent(id)}/actions`, input)).run); },
  async review(id: string, input: HarnessReviewRequest): Promise<HarnessRun> { return readHarnessRun((await request(`${root}/${encodeURIComponent(id)}/reviews`, input)).run); },
};

export function harnessMediaUrl(runId: string, artifactId: string, version: number): string {
  return `${root}/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}/media?version=${version}`;
}
