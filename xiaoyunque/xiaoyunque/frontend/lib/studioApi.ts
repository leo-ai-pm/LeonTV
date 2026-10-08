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
  duration?: number;
  shots?: Array<Record<string, unknown>>;
  error?: string;
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
}

async function studioJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof payload.detail === 'string' ? payload.detail : `请求失败 (${response.status})`);
  }
  return payload as T;
}

export async function listStudioProjects(trashed = false): Promise<StudioProjectSummary[]> {
  const data = await studioJson<{ projects: StudioProjectSummary[] }>(`/api/studio/projects?trashed=${trashed}`);
  return data.projects;
}

export function createStudioProject(title: string): Promise<StudioProject> {
  return studioJson('/api/studio/projects', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }),
  });
}

export function cloneStudioProject(id: string): Promise<StudioProject> {
  return studioJson(`/api/studio/projects/${encodeURIComponent(id)}/clone`, { method: 'POST' });
}

export function getStudioProject(id: string): Promise<StudioProject> {
  return studioJson(`/api/studio/projects/${encodeURIComponent(id)}`);
}

export function saveStudioProject(id: string, revision: number, title: string, nodes: StudioNode[], edges: StudioEdge[]): Promise<StudioProject> {
  return studioJson(`/api/studio/projects/${encodeURIComponent(id)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_revision: revision, title, nodes, edges }),
  });
}

export function trashStudioProject(id: string): Promise<StudioProject> {
  return studioJson(`/api/studio/projects/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function restoreStudioProject(id: string): Promise<StudioProject> {
  return studioJson(`/api/studio/projects/${encodeURIComponent(id)}/restore`, { method: 'POST' });
}

export function mediaUrl(path?: string): string {
  if (!path) return '';
  if (/^https?:\/\//i.test(path)) return path;
  if (path.startsWith('/code/')) return path;
  if (path.startsWith('code/')) return `/${path}`;
  if (path.startsWith('result/')) return `/code/${path}`;
  return `/code/result/${path.replace(/^\/+/, '')}`;
}
