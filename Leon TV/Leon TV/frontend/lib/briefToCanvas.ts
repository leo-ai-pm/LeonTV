/**
 * 创作简报 → 画布骨架。
 *
 * 只按简报与镜数生成空的分镜/参考图/视频节点，不调用模型、不产生费用。
 * 生成的节点都是 idle 状态；用户逐镜填写、生成或导入素材后才可导出成片。
 */
import type { StudioEdge, StudioNode, StudioNodeData } from '@/lib/studioApi';
import type { CreatorBriefDetail } from '@/lib/creatorBriefTypes';

export const MIN_SHOTS = 1;
export const MAX_SHOTS = 6;

export interface BriefCanvasPlan {
  title: string;
  nodes: StudioNode[];
  edges: StudioEdge[];
}

/** 由模型产出的分镜内容；缺省时退化为空骨架。 */
export interface ShotContent {
  title?: string;
  description: string;
  visualPrompt?: string;
  duration?: number;
}

/** 把总时长按镜数拆成整数秒，余数分给前面的镜头。 */
export function splitDurations(totalSeconds: number, shots: number): number[] {
  const count = Math.trunc(shots);
  if (!Number.isFinite(totalSeconds) || totalSeconds <= 0 || count < 1) return [];
  const total = Math.trunc(totalSeconds);
  const base = Math.floor(total / count);
  const remainder = total - base * count;
  return Array.from({ length: count }, (_, index) => base + (index < remainder ? 1 : 0));
}

export function clampShots(value: number): number {
  if (!Number.isFinite(value)) return 3;
  return Math.min(MAX_SHOTS, Math.max(MIN_SHOTS, Math.trunc(value)));
}

function node(id: string, kind: StudioNodeData['kind'], title: string, x: number, y: number, extra: Partial<StudioNodeData> = {}): StudioNode {
  return {
    id,
    type: 'studio',
    position: { x, y },
    data: { kind, title, description: '', status: 'idle', ...extra },
  };
}

function edge(source: string, target: string): StudioEdge {
  return { id: `${source}--${target}`, source, target, type: 'smoothstep' };
}

export function buildBriefCanvas(brief: CreatorBriefDetail, shots: number, content: ShotContent[] = []): BriefCanvasPlan {
  const count = clampShots(shots);
  const durations = splitDurations(brief.production.duration_seconds, count);
  const requirements = brief.requirements.trim();
  const script = brief.script.trim();

  const nodes: StudioNode[] = [
    node('brief', 'text', brief.title.trim() || '创作简报', 60, 80, {
      description: requirements ? `${script}\n\n制作要求：\n${requirements}` : script,
      status: 'completed',
    }),
  ];
  const edges: StudioEdge[] = [];

  for (let index = 0; index < count; index += 1) {
    const shot = index + 1;
    const y = 80 + index * 240;
    const shotId = `shot-${shot}`;
    const frameId = `frame-${shot}`;
    const clipId = `clip-${shot}`;
    const shotContent = content[index];
    const duration = shotContent?.duration && shotContent.duration > 0 ? shotContent.duration : durations[index];
    const description = shotContent?.description?.trim() || '';
    nodes.push(
      node(shotId, 'storyboard', shotContent?.title?.trim() || `镜头${shot} · 待填写`, 430, y, {
        description,
        visualPrompt: shotContent?.visualPrompt?.trim() || '',
        duration,
        status: description ? 'completed' : 'idle',
      }),
      node(frameId, 'image', `参考图${shot} · 待生成`, 800, y, {
        description: shotContent?.visualPrompt?.trim() || description,
        ratio: brief.production.ratio,
        status: 'idle',
      }),
      node(clipId, 'video', `视频${shot} · 待生成`, 1170, y, {
        description: description || '',
        duration,
        ratio: brief.production.ratio,
        video_ratio: brief.production.ratio,
        status: 'idle',
      }),
    );
    edges.push(edge('brief', shotId), edge(shotId, frameId), edge(frameId, clipId));
  }

  return { title: `${brief.title.trim() || '未命名创作简报'} · 画布`, nodes, edges };
}
