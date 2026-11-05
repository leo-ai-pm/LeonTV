/**
 * 全局任务队列：把「正在执行」和「最近完成」的沙盒任务合成一条有序列表。
 * 纯逻辑，便于测试；不发起任何请求，也不改变任务状态。
 */

export type TaskState = 'running' | 'completed' | 'unknown';

export interface TaskEntry {
  id: string;
  tool: string;
  model: string;
  state: TaskState;
  createdAt: string;
  summary: string;
}

export interface RawTask {
  id?: unknown;
  tool?: unknown;
  model?: unknown;
  status?: unknown;
  created_at?: unknown;
  input?: unknown;
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function summaryOf(input: unknown): string {
  if (!input || typeof input !== 'object') return '';
  const record = input as Record<string, unknown>;
  const candidate = text(record.prompt) || text(record.text) || text(record.title);
  const clean = candidate.replace(/\s+/g, ' ').trim();
  return clean.length > 60 ? `${clean.slice(0, 60)}…` : clean;
}

function normalize(raw: RawTask, fallbackState: TaskState): TaskEntry | null {
  const id = text(raw.id);
  if (!id) return null;
  const status = text(raw.status);
  const state: TaskState = fallbackState === 'running' && status !== 'completed'
    ? (status === 'running' || status === '' ? 'running' : 'unknown')
    : 'completed';
  return {
    id,
    tool: text(raw.tool) || 'task',
    model: text(raw.model),
    state,
    createdAt: text(raw.created_at),
    summary: summaryOf(raw.input),
  };
}

export function mergeTaskEntries(active: RawTask[], history: RawTask[], limit = 24): TaskEntry[] {
  const seen = new Set<string>();
  const entries: TaskEntry[] = [];
  for (const raw of active) {
    const entry = normalize(raw, 'running');
    if (!entry || seen.has(entry.id)) continue;
    seen.add(entry.id);
    entries.push(entry);
  }
  for (const raw of history) {
    const entry = normalize(raw, 'completed');
    if (!entry || seen.has(entry.id)) continue;
    seen.add(entry.id);
    entries.push(entry);
  }
  entries.sort((a, b) => {
    if (a.state !== b.state) return a.state === 'running' ? -1 : 1;
    return a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0;
  });
  return entries.slice(0, Math.max(0, limit));
}

export function taskCounts(entries: TaskEntry[]): { running: number; completed: number } {
  return {
    running: entries.filter(entry => entry.state === 'running').length,
    completed: entries.filter(entry => entry.state === 'completed').length,
  };
}

export const TOOL_LABELS: Record<string, string> = {
  t2i: '文生图', i2i: '图生图', video: '视频', llm: '文本', vlm: '图片理解',
};
