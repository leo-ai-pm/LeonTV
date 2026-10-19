import type { CreatorBriefDetail, CreatorBriefList, CreatorBriefSummary, CreatorBriefWriteResult, CreatorCreateRequest, CreatorUpdateRequest } from '@/lib/creatorBriefTypes';

export class CreatorBriefError extends Error {
  constructor(public status: number, public code: string, message: string, public currentRevision?: number) { super(message); }
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('创作简报响应格式错误。');
  return value as Record<string, unknown>;
}
function integer(value: unknown): value is number { return typeof value === 'number' && Number.isInteger(value) && value >= 1; }
export function readCreatorSummary(value: unknown): CreatorBriefSummary {
  const data = object(value), production = object(data.production);
  if (typeof data.id !== 'string' || !/^[a-f0-9]{32}$/.test(data.id) || !integer(data.revision) || typeof data.title !== 'string' ||
      production.duration_seconds !== 15 || production.ratio !== '9:16' || production.style !== 'realistic_cinematic' ||
      data.currency !== 'CNY' || data.authorization_status !== 'not_authorized' || data.execution_enabled !== false ||
      !(data.budget_cny === null || typeof data.budget_cny === 'string' && /^\d+\.\d{2}$/.test(data.budget_cny)) ||
      data.budget_status !== (data.budget_cny === null ? 'pending' : 'draft') || typeof data.source_hash !== 'string' ||
      !/^[a-f0-9]{64}$/.test(data.source_hash) || !integer(data.source_version) || !Number.isInteger(data.script_characters) ||
      typeof data.created_at !== 'string' || typeof data.updated_at !== 'string') throw new Error('创作简报响应与 D07 v1 不一致，已停止操作。');
  // Deliberately project the summary: list/by-key consumers never receive body fields.
  return { id: data.id, revision: data.revision, title: data.title, production: { duration_seconds: 15, ratio: '9:16', style: 'realistic_cinematic' },
    budget_cny: data.budget_cny as string | null, currency: 'CNY', budget_status: data.budget_status as 'pending' | 'draft',
    authorization_status: 'not_authorized', execution_enabled: false, source_hash: data.source_hash, source_version: data.source_version,
    script_characters: data.script_characters as number, created_at: data.created_at, updated_at: data.updated_at };
}
export function readCreatorDetail(value: unknown): CreatorBriefDetail {
  const data = object(value), summary = readCreatorSummary(data);
  if (typeof data.script !== 'string' || typeof data.requirements !== 'string' || typeof data.client_request_key !== 'string' ||
      !integer(data.current_revision) || data.current_revision < summary.revision || data.is_current !== (data.current_revision === summary.revision) ||
      !Array.isArray(data.versions)) throw new Error('创作简报正文格式错误，已停止操作。');
  const versions = data.versions.map(value => {
    const item = object(value);
    if (!integer(item.revision) || !integer(item.source_version) || typeof item.source_hash !== 'string' || typeof item.saved_at !== 'string') throw new Error('简报版本记录格式错误。');
    return { revision: item.revision, source_version: item.source_version, source_hash: item.source_hash, saved_at: item.saved_at };
  });
  return { ...summary, script: data.script, requirements: data.requirements, client_request_key: data.client_request_key,
    current_revision: data.current_revision, is_current: data.is_current as boolean, versions };
}
async function request(url: string, method = 'GET', body?: unknown): Promise<Record<string, unknown>> {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 15000);
  try {
  const response = await fetch(url, { method, signal: abort.signal, cache: 'no-store', ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const payload = data && typeof data === 'object' ? data as Record<string, unknown> : {};
    const detail = payload.detail && typeof payload.detail === 'object' ? payload.detail as Record<string, unknown> : {};
    const messages: Record<string, string> = {
      REVISION_CONFLICT: '服务器已有更新版本，你的修改仍保留。请核对当前版本后再保存。',
      KEY_CONFLICT: '原请求键已关联保存内容，请先查回原记录，不能换键重建。',
      INVALID_INPUT: '简报字段不符合当前要求，请检查剧本、预算与制作规格。',
      NOT_FOUND: '暂未查到该简报、请求键或版本。',
      PRIVATE_STORAGE_UNAVAILABLE: '私有保存服务暂不可用，请保留输入并稍后查回。',
      CORRUPT_STORAGE: '私有简报记录无法校验，已停止读写；请保留输入并核查原记录。',
    };
    const code = typeof detail.code === 'string' ? detail.code : `HTTP_${response.status}`;
    throw new CreatorBriefError(response.status, code, messages[code] || `简报请求失败（HTTP ${response.status}）。`, integer(detail.current_revision) ? detail.current_revision : undefined);
  }
  return object(data);
  } catch (error) {
    if (abort.signal.aborted) throw new CreatorBriefError(0, 'OUTCOME_UNKNOWN', method === 'GET' ? '查询超时，请稍后重查原记录。' : '保存响应超时，结果尚未确认。原请求键和本页原稿已保留，请查回后再操作。');
    if (error instanceof TypeError) throw new CreatorBriefError(0, 'OUTCOME_UNKNOWN', method === 'GET' ? '网络连接中断，请稍后重查原记录。' : '网络连接中断，保存结果待确认，请查回原请求。');
    throw error;
  } finally { clearTimeout(timer); }
}
function writeResult(data: Record<string, unknown>): CreatorBriefWriteResult {
  if (typeof data.replayed !== 'boolean' || !integer(data.applied_revision)) throw new Error('简报保存回执格式错误，请查回原请求。');
  return { brief: readCreatorDetail(data.brief), replayed: data.replayed, applied_revision: data.applied_revision };
}
const root = '/api/creator/briefs';
export const creatorBriefApi = {
  async list(offset = 0): Promise<CreatorBriefList> {
    const data = await request(`${root}?offset=${offset}&limit=50`);
    if (!Array.isArray(data.briefs) || typeof data.has_more !== 'boolean' || !(data.next_offset === null || typeof data.next_offset === 'number' && Number.isInteger(data.next_offset) && data.next_offset >= 0)) throw new Error('简报列表格式错误。');
    return { briefs: data.briefs.map(readCreatorSummary), has_more: data.has_more, next_offset: data.next_offset as number | null };
  },
  async byKey(key: string): Promise<CreatorBriefSummary> { return readCreatorSummary((await request(`${root}/by-key/${encodeURIComponent(key)}`)).brief); },
  async get(id: string, revision?: number): Promise<CreatorBriefDetail> { return readCreatorDetail((await request(`${root}/${encodeURIComponent(id)}${revision === undefined ? '' : `?revision=${revision}`}`)).brief); },
  async create(input: CreatorCreateRequest): Promise<CreatorBriefWriteResult> { return writeResult(await request(root, 'POST', input)); },
  async update(id: string, input: CreatorUpdateRequest): Promise<CreatorBriefWriteResult> { return writeResult(await request(`${root}/${encodeURIComponent(id)}`, 'PUT', input)); },
};
