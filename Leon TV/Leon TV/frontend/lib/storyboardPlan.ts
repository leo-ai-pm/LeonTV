/**
 * 真实分镜：把已保存的创作简报交给文本模型，产出可编辑的镜头表。
 *
 * 模型只返回结构化 JSON；解析端严格校验镜数、时长与内容，不合格就整体失败，
 * 由调用方决定退回空骨架——不会把半成品或伪造的镜头写进画布。
 */
import type { CreatorBriefDetail } from '@/lib/creatorBriefTypes';
import type { ShotContent } from '@/lib/briefToCanvas';

export const STORYBOARD_MODEL = 'deepseek-v4-flash';

const MAX_SHOTS = 6;
const MIN_SHOTS = 1;

export function buildStoryboardPrompt(brief: CreatorBriefDetail, shots: number, totalSeconds: number): string {
  const requirements = brief.requirements.trim();
  return [
    '你是短片分镜师。请把下面的剧本拆成恰好 ' + shots + ' 个镜头，总时长 ' + totalSeconds + ' 秒。',
    '只输出一个 JSON 对象，不要 markdown 代码块，不要任何额外说明。',
    '格式：{"shots":[{"title":"镜头名（不超过12字）","description":"画面与动作，一句话","visualPrompt":"用于文生图的画面描述，包含景别、光线、人物状态","duration":整数秒}]}',
    '规则：',
    '- shots 数组长度必须等于 ' + shots + '；duration 之和必须等于 ' + totalSeconds + '。',
    '- 每个 description 与 visualPrompt 都必填，不得为空。',
    '- 保持人物、服装、场景在各镜之间连续；不要写台词字幕要求。',
    '',
    `作品名称：${brief.title}`,
    '剧本：',
    brief.script,
    ...(requirements ? ['', '制作要求：', requirements] : []),
  ].join('\n');
}

function extractJson(raw: string): unknown {
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const text = (fenced ? fenced[1] : raw).trim();
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start < 0 || end <= start) return null;
  try {
    return JSON.parse(text.slice(start, end + 1));
  } catch {
    return null;
  }
}

export interface StoryboardResult {
  shots: ShotContent[];
  error?: string;
}

function clamp(text: string, limit: number): string {
  const clean = text.replace(/\s+/g, ' ').trim();
  return clean.length > limit ? clean.slice(0, limit) : clean;
}

/** 严格校验模型输出；任何一条不合格都整体判失败。 */
export function parseStoryboard(raw: string, shots: number, totalSeconds: number): StoryboardResult {
  const expected = Math.min(MAX_SHOTS, Math.max(MIN_SHOTS, Math.trunc(shots)));
  const data = extractJson(raw);
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    return { shots: [], error: '模型没有返回可解析的分镜 JSON。' };
  }
  const list = (data as Record<string, unknown>).shots;
  if (!Array.isArray(list)) return { shots: [], error: '模型返回缺少 shots 数组。' };
  if (list.length !== expected) {
    return { shots: [], error: `模型返回了 ${list.length} 个镜头，与要求的 ${expected} 个不一致。` };
  }
  const parsed: ShotContent[] = [];
  let durationSum = 0;
  for (const [index, item] of list.entries()) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      return { shots: [], error: `第 ${index + 1} 个镜头不是对象。` };
    }
    const shot = item as Record<string, unknown>;
    const description = typeof shot.description === 'string' ? clamp(shot.description, 400) : '';
    const visualPrompt = typeof shot.visualPrompt === 'string' ? clamp(shot.visualPrompt, 400) : '';
    if (!description) return { shots: [], error: `第 ${index + 1} 个镜头缺少 description。` };
    if (!visualPrompt) return { shots: [], error: `第 ${index + 1} 个镜头缺少 visualPrompt。` };
    const rawDuration = typeof shot.duration === 'number' ? Math.trunc(shot.duration) : NaN;
    if (!Number.isFinite(rawDuration) || rawDuration < 1 || rawDuration > totalSeconds) {
      return { shots: [], error: `第 ${index + 1} 个镜头的 duration 不合法。` };
    }
    durationSum += rawDuration;
    parsed.push({
      title: typeof shot.title === 'string' ? clamp(shot.title, 20) : '',
      description,
      visualPrompt,
      duration: rawDuration,
    });
  }
  if (durationSum !== totalSeconds) {
    return { shots: [], error: `模型分镜总时长 ${durationSum} 秒，与要求的 ${totalSeconds} 秒不一致。` };
  }
  return { shots: parsed };
}
