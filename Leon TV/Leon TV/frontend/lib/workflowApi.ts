import type { WorkflowArtifact, WorkflowSnapshot, SessionSummary, StreamData, TaskInput } from '@/lib/workflowTypes';
import { requestJson, readJsonResponse } from '@/lib/http';

/**
 * 工作流 API 客户端
 */

// Preserve the existing direct streaming endpoint; its origin must be allowed by the backend.
const STREAM_API_BASE = (process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

export interface StageInfo {
  id: string;
  name: string;
  order: number;
  description: string;
}

export type ProjectStatus = WorkflowSnapshot;

export interface StreamEvent {
  type: 'progress' | 'heartbeat' | 'stage_complete' | 'error' | 'content';
  message?: string;
  phase?: string;
  step_desc?: string;
  percent?: number;
  stage?: string;
  status?: string;
  requires_intervention?: boolean;
  payload_summary?: unknown;
  content?: string;
  time?: number;
  data?: StreamData;
}

export interface PipelineTask {
  task_id: string;
  pipeline: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | string;
  progress?: number;
  message?: string;
  input?: TaskInput;
  output?: TaskInput;
  artifacts?: Array<{ kind: string; name?: string; path: string; exists?: boolean; created_at?: string }>;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
  output_dir?: string;
}

export interface SandboxTask {
  id: string;
  tool: string;
  model: string;
  input?: TaskInput;
  status: string;
  progress?: number;
  created_at?: string;
}

export interface PipelineStartResponse {
  task_id: string;
  pipeline: string;
  status: string;
  metadata_url: string;
  output_dir: string;
}

export interface PipelineTaskEvent {
  type: 'snapshot' | 'progress' | 'artifact' | 'completed' | 'failed';
  task_id: string;
  status?: string;
  progress?: number;
  artifact?: { kind: string; name?: string; path: string; exists?: boolean; created_at?: string };
}

export interface ApiModelOption {
  id: string;
  label: string;
  provider: string;
  family?: string;
  media_type?: 'image' | 'video';
  model_type?: 'llm' | 'vlm' | 't2i' | 'i2i' | 'video';
  type?: string[];
  ability_type?: string;
  ability_types?: string[];
  adapter_ability_types?: string[];
  input_modalities?: string[];
  adapter_input_modalities?: string[];
  api_contract_verified?: boolean;
  capabilities?: Record<string, unknown>;
}

export interface StandardTemplateOption {
  id: string;
  name: string;
  label: string;
  size: string;
  ratio: '9:16' | '1:1' | '16:9' | string;
  width: number;
  height: number;
  media_width: number;
  media_height: number;
  media_ratio: string;
  media_resolution: string;
  supports_video?: boolean;
  fields: Array<{ key: string; type: string; default: string }>;
  preview_url: string;
}

async function requireOkResponse(response: Response, fallbackMessage: string): Promise<Response> {
  if (!response.ok) await readJsonResponse(response);
  if (!response.body) throw new Error(fallbackMessage);
  return response;
}

export async function fetchStages(): Promise<StageInfo[]> {
  return (await requestJson<{ stages: StageInfo[] }>('/api/stages')).stages;
}

export async function fetchSessions(): Promise<SessionSummary[]> {
  return (await requestJson<{ sessions: SessionSummary[] }>('/api/sessions')).sessions || [];
}

function postPipelineTask(path: string, params: Record<string, unknown>): Promise<PipelineStartResponse> {
  return requestJson(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params) });
}

export function startStandardPipeline(params: Record<string, unknown>): Promise<PipelineStartResponse> {
  return postPipelineTask('/api/pipelines/standard/tasks', params);
}
export function startActionTransferPipeline(params: Record<string, unknown>): Promise<PipelineStartResponse> {
  return postPipelineTask('/api/pipelines/action_transfer/tasks', params);
}
export function startDigitalHumanPipeline(params: Record<string, unknown>): Promise<PipelineStartResponse> {
  return postPipelineTask('/api/pipelines/digital_human/tasks', params);
}

export async function fetchPipelineTasks(limit = 100): Promise<PipelineTask[]> {
  return (await requestJson<{ tasks: PipelineTask[] }>(`/api/tasks?limit=${limit}`)).tasks || [];
}
export function fetchPipelineTask(taskId: string): Promise<PipelineTask> {
  return requestJson(`/api/tasks/${encodeURIComponent(taskId)}`);
}
export async function fetchSandboxTasks(): Promise<SandboxTask[]> {
  return (await requestJson<{ tasks: SandboxTask[] }>('/api/sandbox/tasks')).tasks || [];
}
export function clearTempCache(): Promise<{ status: string; deleted: number; freed_bytes?: number; freed_mb?: number; errors?: Array<{ path: string; error: string }> }> {
  return requestJson('/api/cache/temp', { method: 'DELETE' });
}
export async function deletePipelineTask(taskId: string): Promise<void> {
  await requestJson(`/api/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' });
}

const modelRequests = new Map<string, Promise<ApiModelOption[]>>();
export function fetchApiModels(params: {
  mediaType?: 'image' | 'video';
  modelType?: 'llm' | 'vlm' | 't2i' | 'i2i' | 'video';
  ability?: string;
  verifiedOnly?: boolean;
} = {}): Promise<ApiModelOption[]> {
  const search = new URLSearchParams();
  if (params.mediaType) search.set('media_type', params.mediaType);
  if (params.modelType) search.set('model_type', params.modelType);
  if (params.ability) search.set('ability', params.ability);
  if (params.verifiedOnly) search.set('verified_only', 'true');
  const url = `/api/models${search.size ? `?${search}` : ''}`;
  const existing = modelRequests.get(url);
  if (existing) return existing;
  const request = requestJson<{ models: ApiModelOption[] }>(url)
    .then(data => data.models || [])
    .finally(() => modelRequests.delete(url));
  modelRequests.set(url, request);
  return request;
}
export async function fetchStandardTemplates(): Promise<StandardTemplateOption[]> {
  return (await requestJson<{ templates: StandardTemplateOption[] }>('/api/pipelines/standard/templates')).templates || [];
}
export function uploadMedia(file: File): Promise<{ filename: string; file_path: string }> {
  const body = new FormData();
  body.append('file', file);
  return requestJson('/api/upload_media', { method: 'POST', body });
}
export function uploadArtifactImage(
  sessionId: string, stage: string, itemType: string, itemId: string, file: File,
): Promise<{ status: string; path: string; artifact: WorkflowArtifact; status_map: Record<string, string> }> {
  const body = new FormData();
  body.append('item_type', itemType);
  body.append('item_id', itemId);
  body.append('file', file);
  return requestJson(`/api/project/${encodeURIComponent(sessionId)}/artifact/${encodeURIComponent(stage)}/upload_image`, { method: 'POST', body });
}

export function subscribePipelineTask(
  taskId: string,
  onEvent: (event: PipelineTaskEvent) => void,
  onError?: () => void,
): () => void {
  const source = new EventSource(`${STREAM_API_BASE}/api/tasks/${taskId}/events`);
  source.onmessage = event => {
    try {
      onEvent(JSON.parse(event.data));
    } catch {
      // Ignore malformed stream events.
    }
  };
  source.onerror = () => {
    onError?.();
    source.close();
  };
  return () => source.close();
}

export async function startProject(params: {
  idea: string;
  file_path?: string;
  style?: string;
  video_ratio?: string;
  video_resolution?: string;
  llm_model?: string;
  vlm_model?: string;
  image_t2i_model?: string;
  image_it2i_model?: string;
  video_model?: string;
  video_first_frame_model?: string;
  video_start_end_model?: string;
  video_reference_model?: string;
  video_generation_mode?: string;
  scene_number?: number;
  enable_concurrency?: boolean;
  web_search?: boolean;
  expand_idea?: boolean;
  episodes?: number;
}): Promise<{ session_id: string; status: string; params: Record<string, unknown> }> {
  const resp = await fetch('/api/project/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  return readJsonResponse(resp);
}

export async function getProjectStatus(sessionId: string): Promise<ProjectStatus> {
  const resp = await fetch(`/api/project/${sessionId}/status`);
  return readJsonResponse(resp);
}

// 兼容旧路由名；后端实际会通过统一的 workflow state 入口返回状态。
export async function getProjectStatusFromDisk(sessionId: string): Promise<WorkflowSnapshot> {
  const resp = await fetch(`/api/project/${sessionId}/status/from_disk`);
  return readJsonResponse(resp);
}

export async function getArtifact(sessionId: string, stage: string): Promise<{ artifact: WorkflowArtifact }> {
  const resp = await fetch(`/api/project/${sessionId}/artifact/${stage}`);
  return readJsonResponse(resp);
}

export async function checkSceneAssets(sessionId: string, sceneNumber: number): Promise<{
  scene_number: number;
  reference_images: number;
  videos: number;
  shot_count: number;
}> {
  const resp = await fetch(`/api/project/${sessionId}/scene/${sceneNumber}/assets`);
  return readJsonResponse(resp);
}

export async function executeStage(
  sessionId: string,
  stage: string,
  inputData: Record<string, unknown> = {},
  signal?: AbortSignal,
): Promise<Response> {
  const response = await fetch(`${STREAM_API_BASE}/api/project/${sessionId}/execute/${stage}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(inputData),
    signal,
  });
  return requireOkResponse(response, `阶段 ${stage} 启动失败`);
}

export async function intervene(
  sessionId: string,
  stage: string,
  modifications: Record<string, unknown>,
): Promise<Response> {
  // Use STREAM_API_BASE to bypass Next.js proxy (SSE endpoint)
  const response = await fetch(`${STREAM_API_BASE}/api/project/${sessionId}/intervene`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stage, modifications }),
  });
  return requireOkResponse(response, `阶段 ${stage} 修改失败`);
}

export async function stopProject(sessionId: string): Promise<{ status: string }> {
  const resp = await fetch(`/api/project/${sessionId}/stop`, {
    method: 'POST',
  });
  await requireOkResponse(resp, '停止项目失败');
  return resp.json();
}

export async function updateModels(
  sessionId: string,
  models: Partial<Record<string, string | boolean>>,
): Promise<{ status: string }> {
  const resp = await fetch(`/api/project/${sessionId}/models`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(models),
  });
  await requireOkResponse(resp, '更新项目模型失败');
  return resp.json();
}

export async function deleteSession(
  sessionId: string,
): Promise<{ status: string }> {
  const resp = await fetch(`/api/sessions/${sessionId}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
  });
  return readJsonResponse(resp);
}

export async function saveSelections(
  sessionId: string,
  stage: string,
  selections: Record<string, unknown>,
): Promise<{ status: string; artifact?: WorkflowArtifact; status_map?: Record<string, string> }> {
  const resp = await fetch(`/api/project/${sessionId}/artifact/${stage}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(selections),
  });
  return readJsonResponse(resp);
}

export async function continueWorkflow(sessionId: string): Promise<{ status: string; next_stage?: string; status_map?: Record<string, string> }> {
  const resp = await fetch(`/api/project/${sessionId}/continue`, {
    method: 'POST',
  });
  await requireOkResponse(resp, '继续工作流失败');
  const result = await readJsonResponse<{ status: string; next_stage?: string; status_map?: Record<string, string>; message?: string }>(resp);
  if (result.status === 'error') throw new Error(result.message || '当前状态不允许继续');
  return result;
}

export async function* parseStreamEvents(response: Response): AsyncGenerator<StreamEvent> {
  if (!response.body) throw new Error('阶段事件流没有响应体');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let terminalEventReceived = false;
  function parse(line: string): StreamEvent | null {
    if (!line.trim()) return null;
    try {
      const event = JSON.parse(line) as StreamEvent | null;
      if (!event || typeof event.type !== 'string') return null;
      if (event.type === 'stage_complete' || event.type === 'error') terminalEventReceived = true;
      return event.type === 'heartbeat' ? null : event;
    } catch { return null; }
  }
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        const event = parse(line);
        if (event) yield event;
      }
      if (done) break;
    }
    const trailing = parse(buffer);
    if (trailing) yield trailing;
    if (!terminalEventReceived) throw new Error('阶段事件流提前结束，未收到完成或错误事件');
  } finally {
    // Closing the view only stops this reader; it does not cancel a persisted job.
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
