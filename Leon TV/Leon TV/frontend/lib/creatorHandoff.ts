/**
 * 创作简报 → 六阶段创作 的只读交接。
 *
 * 只生成六阶段入口的初始文本与画幅，不创建会话、不调用模型、不授权付费。
 * 真正的生成仍然由用户在六阶段页面确认模型后手动开始。
 */
import type { CreatorBriefDetail } from '@/lib/creatorBriefTypes';

export interface CreatorHandoff {
  briefId: string;
  revision: number;
  title: string;
  idea: string;
  videoRatio: '9:16';
}

/** 创作简报固定 9:16；六阶段入口按此比例预选。 */
export const HANDOFF_VIDEO_RATIO = '9:16' as const;

/** D07 简报只允许当前版本被带入；过期或非当前版本一律拒绝。 */
export function buildCreatorHandoff(brief: CreatorBriefDetail): CreatorHandoff | null {
  if (!brief.is_current) return null;
  const script = brief.script.trim();
  if (!script) return null;
  const requirements = brief.requirements.trim();
  const idea = requirements ? `${script}\n\n制作要求：\n${requirements}` : script;
  return {
    briefId: brief.id,
    revision: brief.revision,
    title: brief.title.trim() || '未命名创作简报',
    idea,
    videoRatio: HANDOFF_VIDEO_RATIO,
  };
}

/** 六阶段入口地址；只携带简报 ID，正文不进入 URL。 */
export function creatorHandoffHref(briefId: string): string {
  return `/workflow?brief=${encodeURIComponent(briefId)}`;
}
