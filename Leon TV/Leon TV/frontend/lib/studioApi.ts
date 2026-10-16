import { requestJson } from '@/lib/http';
export { mediaUrl } from '@/lib/media';
import type { Edge, Node } from '@xyflow/react';

export type StudioKind = 'text' | 'script' | 'character' | 'scene' | 'storyboard' | 'image' | 'video' | 'output';

export type StudioNodeData = {
  kind: StudioKind;
  title: string;
  description?: string;
  mediaPath?: string;
  versions?: string[];
  model?: string;
  status?: string;
  sourceStage?: string;
  sourceId?: string;
  visualPrompt?: string;
  ratio?: string;
  video_ratio?: string;
  duration?: number;
  shots?: Array<Record<string, unknown>>;
  error?: string;
  jobId?: string;
} & Record<string, unknown>;

export type StudioNode = Node<StudioNodeData, 'studio'>;
export type StudioEdge = Edge;

export interface StudioProjectSummary {
  id: string;
  title: string;
  kind: 'canvas' | 'workflow';
  updated_at: number;
  node_count: number;
  trashed: boolean;
}

export interface StudioProject extends StudioProjectSummary {
  source_session_id: string | null;
  revision: number;
  created_at: number;
  nodes: StudioNode[];
  edges: StudioEdge[];
  exports?: Array<{ id: string; path: string; clip_ids: string[]; source_revision: number; created_at: number }>;
}

export async function listStudioProjects(trashed = false, signal?: AbortSignal): Promise<StudioProjectSummary[]> {
  const data = await requestJson<{ projects: StudioProjectSummary[] }>(`/api/studio/projects?trashed=${trashed}`, { signal });
  return data.projects;
}

export function createStudioProject(title: string): Promise<StudioProject> {
  return requestJson('/api/studio/projects', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }),
  });
}

export function cloneStudioProject(id: string): Promise<StudioProject> {
  return requestJson(`/api/studio/projects/${encodeURIComponent(id)}/clone`, { method: 'POST' });
}

export function getStudioProject(id: string): Promise<StudioProject> {
  return requestJson(`/api/studio/projects/${encodeURIComponent(id)}`);
}

export function saveStudioProject(id: string, revision: number, title: string, nodes: StudioNode[], edges: StudioEdge[]): Promise<StudioProject> {
  return requestJson(`/api/studio/projects/${encodeURIComponent(id)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_revision: revision, title, nodes, edges }),
  });
}

export function exportStudioProject(id: string, revision: number): Promise<StudioProject> {
  return requestJson(`/api/studio/projects/${encodeURIComponent(id)}/export`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_revision: revision }),
  });
}

export function trashStudioProject(id: string): Promise<StudioProject> {
  return requestJson(`/api/studio/projects/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function restoreStudioProject(id: string): Promise<StudioProject> {
  return requestJson(`/api/studio/projects/${encodeURIComponent(id)}/restore`, { method: 'POST' });
}
