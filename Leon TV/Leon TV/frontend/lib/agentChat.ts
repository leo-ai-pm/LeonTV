/**
 * Agent 对话的提示词与回复解析（纯逻辑）。
 *
 * 服务端只做一次普通文本补全，不执行任何动作；模型返回的结构化动作由前端校验后，
 * 再通过画布命令总线逐条执行。宁可丢弃不合法的动作，也不猜测用户意图。
 */
import type { CanvasBus, CanvasSnapshot } from '@/lib/canvasBus';
import type { StudioKind } from '@/lib/studioApi';

export type AgentAction =
  | { type: 'add_node'; kind: StudioKind; title?: string; description?: string }
  | { type: 'update_node'; id: string; title?: string; description?: string }
  | { type: 'delete_node'; id: string }
  | { type: 'run_node'; id: string }
  | { type: 'export' };

export interface AgentTurn {
  reply: string;
  actions: AgentAction[];
}

/** 对话使用的文本模型；可在设置里换成其它 LLM，此处是默认值。 */
export const AGENT_MODEL = 'deepseek-v4-flash';

const NODE_KINDS: StudioKind[] = ['text', 'storyboard', 'image', 'video'];
const MAX_TITLE = 120;
const MAX_DESCRIPTION = 2000;

function clip(value: string, limit: number): string {
  return value.length > limit ? value.slice(0, limit) : value;
}

export function buildAgentPrompt(snapshot: CanvasSnapshot, question: string): string {
  const lines = snapshot.nodes.map(node => {
    const description = (node.description || '').replace(/\s+/g, ' ').slice(0, 120);
    return `- id=${node.id} | 类型=${node.kind} | 名称=${node.title || '(未命名)'} | 状态=${node.status || 'idle'}` +
      (node.mediaPath ? ` | 已有素材` : '') +
      (description ? ` | 描述=${description}` : '');
  });
  return [
    '你是 LeonTV 画布助手，帮助用户操作当前画布。',
    '只输出一个 JSON 对象，不要 markdown 代码块，不要输出任何额外文字。',
    '格式：{"reply":"给用户看的中文回复","actions":[...]}',
    '可用动作（actions 可以为空）：',
    '{"type":"add_node","kind":"text|storyboard|image|video","title":"可选","description":"可选"}',
    '{"type":"update_node","id":"节点id","title":"可选","description":"可选"}',
    '{"type":"delete_node","id":"节点id"}',
    '{"type":"run_node","id":"节点id"}',
    '{"type":"export"}',
    '规则：只能引用下面列出的节点 id；不确定就不要产生动作；不要编造素材或结果；不要声称已经生成了图片或视频。',
    '',
    `画布：${snapshot.title || '(未命名)'}，共 ${snapshot.nodes.length} 个节点。`,
    ...(lines.length ? lines : ['- (画布为空)']),
    '',
    `用户：${question}`,
  ].join('\n');
}

function extractJsonObject(raw: string): string | null {
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const text = (fenced ? fenced[1] : raw).trim();
  const start = text.indexOf('{');
  if (start < 0) return null;
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let index = start; index < text.length; index += 1) {
    const char = text[index];
    if (inString) {
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === '"') inString = false;
      continue;
    }
    if (char === '"') inString = true;
    else if (char === '{') depth += 1;
    else if (char === '}') {
      depth -= 1;
      if (depth === 0) return text.slice(start, index + 1);
    }
  }
  return null;
}

function readAction(value: unknown, allowedIds: Set<string>): AgentAction | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const item = value as Record<string, unknown>;
  const type = item.type;
  if (type === 'add_node') {
    const kind = String(item.kind || '');
    if (!NODE_KINDS.includes(kind as StudioKind)) return null;
    const title = typeof item.title === 'string' ? clip(item.title.trim(), MAX_TITLE) : undefined;
    const description = typeof item.description === 'string' ? clip(item.description.trim(), MAX_DESCRIPTION) : undefined;
    return {
      type: 'add_node', kind: kind as StudioKind,
      ...(title ? { title } : {}),
      ...(description ? { description } : {}),
    };
  }
  if (type === 'update_node') {
    const id = typeof item.id === 'string' ? item.id : '';
    if (!allowedIds.has(id)) return null;
    const title = typeof item.title === 'string' ? clip(item.title.trim(), MAX_TITLE) : undefined;
    const description = typeof item.description === 'string' ? clip(item.description.trim(), MAX_DESCRIPTION) : undefined;
    if (title === undefined && description === undefined) return null;
    return { type: 'update_node', id, ...(title !== undefined ? { title } : {}), ...(description !== undefined ? { description } : {}) };
  }
  if (type === 'delete_node' || type === 'run_node') {
    const id = typeof item.id === 'string' ? item.id : '';
    if (!allowedIds.has(id)) return null;
    return { type, id };
  }
  if (type === 'export') return { type: 'export' };
  return null;
}

/** 解析模型回复；无法解析时把原文当作回复，不产生任何动作。 */
export function parseAgentReply(raw: string, allowedIds: Set<string>): AgentTurn {
  const source = String(raw || '').trim();
  const json = extractJsonObject(source);
  if (!json) return { reply: source, actions: [] };
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch {
    return { reply: source, actions: [] };
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return { reply: source, actions: [] };
  const data = parsed as Record<string, unknown>;
  const reply = typeof data.reply === 'string' ? data.reply.trim() : '';
  const list = Array.isArray(data.actions) ? data.actions : [];
  const actions = list.map(item => readAction(item, allowedIds)).filter((item): item is AgentAction => Boolean(item));
  return { reply: reply || source, actions };
}

export function describeAction(action: AgentAction, nodes: Map<string, string>): string {
  switch (action.type) {
    case 'add_node': return `新增${action.kind === 'storyboard' ? '分镜' : action.kind === 'image' ? '参考图' : action.kind === 'video' ? '视频' : '文本'}节点${action.title ? `「${action.title}」` : ''}`;
    case 'update_node': return `修改「${nodes.get(action.id) || action.id}」`;
    case 'delete_node': return `删除「${nodes.get(action.id) || action.id}」`;
    case 'run_node': return `生成「${nodes.get(action.id) || action.id}」`;
    case 'export': return '导出成片';
    default: return '未知动作';
  }
}


/** 把模型给出的动作按顺序交给画布执行，返回用户可读的执行说明。 */
export function applyAgentActions(bus: CanvasBus, actions: AgentAction[], names: Map<string, string>): string[] {
  const applied: string[] = [];
  for (const action of actions) {
    const ok = (() => {
      switch (action.type) {
        case 'add_node': return bus.dispatch({ type: 'add-node', kind: action.kind, patch: { ...(action.title ? { title: action.title } : {}), ...(action.description ? { description: action.description } : {}) } });
        case 'update_node': {
          const patch: { title?: string; description?: string } = {};
          if (action.title !== undefined) patch.title = action.title;
          if (action.description !== undefined) patch.description = action.description;
          return bus.dispatch({ type: 'update-node', id: action.id, patch });
        }
        case 'delete_node': return bus.dispatch({ type: 'delete-node', id: action.id });
        case 'run_node': return bus.dispatch({ type: 'run-node', id: action.id });
        case 'export': return bus.dispatch({ type: 'export-video' });
        default: return false;
      }
    })();
    if (ok) applied.push(describeAction(action, names));
  }
  return applied;
}
