import type { ProjectParams } from '@/components/HomePage';

export interface WorkflowAsset {
  id: string;
  name: string;
  title?: string;
  description: string;
  selected: string;
  versions: string[];
  status?: 'pending' | 'done' | 'failed' | 'running';
  index?: number;
  episode?: number;
  episode_title?: string;
  duration?: number;
}
export interface WorkflowEpisode {
  episode_number?: number;
  episode?: number;
  act_title?: string;
  title?: string;
  segments?: Array<Record<string, unknown>>;
}
export interface WorkflowArtifact {
  [key: string]: unknown;
  phase?: string;
  characters?: WorkflowAsset[];
  settings?: WorkflowAsset[];
  scenes?: WorkflowAsset[];
  clips?: WorkflowAsset[];
  images?: WorkflowAsset[];
  shots?: Array<Record<string, unknown>>;
  episodes?: WorkflowEpisode[];
  completed_acts?: unknown[];
  scene_images?: Record<string, string>;
  video_clips?: Record<string, string>;
  final_videos?: Array<{ name?: string; path: string; episode?: number }>;
  final_video?: string;
}
export interface SessionSummary {
  id: string;
  title?: string;
  idea?: string;
  style?: string;
  date?: number;
  status?: Record<string, string>;
}
export interface WorkflowSnapshot extends Partial<ProjectParams> {
  session_id: string;
  current_stage: string;
  status: Record<string, string>;
  artifacts?: Record<string, WorkflowArtifact>;
  stage_progress?: Record<string, { percent?: number; message?: string; step?: string }>;
  meta?: Partial<ProjectParams> & { user_textbox_input?: string };
  user_textbox_input?: string;
  error: string | null;
}
export interface StreamData {
  assets_preview?: WorkflowArtifact;
  asset_complete?: Partial<WorkflowAsset> & { id: string; type: 'characters' | 'settings' | 'scenes' | 'clips' | 'images' };
  beat_sheet?: unknown;
  act_complete?: unknown;
  scene_shots_complete?: { shots: Array<Record<string, unknown>> };
}
export interface TaskInput {
  [key: string]: unknown;
  title?: string;
  goods_title?: string;
  text?: string;
  prompt_text?: string;
  goods_text?: string;
  prompt?: string;
  reference_image?: string;
  images?: string[];
}
