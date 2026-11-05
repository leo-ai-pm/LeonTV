'use client';

import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, ChevronDown, ChevronUp, Clock, ListOrdered, Loader2, RefreshCw } from 'lucide-react';
import { mergeTaskEntries, taskCounts, TOOL_LABELS, type TaskEntry } from '@/lib/taskCenter';

/**
 * 全局任务队列（只读）。
 * 汇总当前正在生成的沙盒任务与最近完成记录；不重启、不取消、不改写任何任务状态。
 */
export default function TaskCenter() {
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState<TaskEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const [activeResponse, historyResponse] = await Promise.all([
        fetch('/api/sandbox/tasks', { cache: 'no-store' }),
        fetch('/api/sandbox/history', { cache: 'no-store' }),
      ]);
      const activeJson = activeResponse.ok ? await activeResponse.json() : { tasks: [] };
      const historyJson = historyResponse.ok ? await historyResponse.json() : { records: [] };
      const active = Array.isArray(activeJson?.tasks) ? activeJson.tasks : [];
      const records = Array.isArray(historyJson?.records) ? historyJson.records : [];
      setEntries(mergeTaskEntries(active, records));
      setError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '任务列表暂不可用。');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    void Promise.resolve().then(() => { if (active) return load(); });
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 4000);
    return () => { active = false; window.clearInterval(timer); };
  }, [load]);

  const counts = taskCounts(entries);

  return (
    <div className="ws-taskbar" data-open={open}>
      <div className="ws-taskbar-head">
        <button type="button" className="ws-taskbar-title" onClick={() => setOpen(value => !value)} aria-expanded={open}>
          <ListOrdered size={14} /> 任务队列
        </button>
        <span className="ws-taskbar-counts">
          {counts.running > 0 && <span className="ws-taskbar-running"><Loader2 size={11} className="animate-spin" /> {counts.running} 进行中</span>}
          <span className="ws-taskbar-done"><CheckCircle2 size={11} /> {counts.completed} 已完成</span>
        </span>
        <button type="button" className="ws-taskbar-refresh" aria-label="刷新任务" onClick={() => { setRefreshing(true); void load(); }} disabled={refreshing}>
          <RefreshCw size={12} className={refreshing ? 'animate-spin' : undefined} />
        </button>
        <button type="button" className="ws-taskbar-toggle" aria-label={open ? '收起任务队列' : '展开任务队列'} onClick={() => setOpen(value => !value)}>
          {open ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
        </button>
      </div>

      {open && (
        <div className="ws-taskbar-body">
          {loading && <p className="ws-taskbar-note"><Loader2 size={12} className="animate-spin" /> 正在读取任务…</p>}
          {!loading && error && <p className="ws-taskbar-note ws-taskbar-error">{error}</p>}
          {!loading && !error && entries.length === 0 && <p className="ws-taskbar-note">还没有生成任务。在画布上生成图片或视频后会出现在这里。</p>}
          {!loading && entries.length > 0 && (
            <ul className="ws-task-list">
              {entries.map(entry => (
                <li key={entry.id} className="ws-task" data-state={entry.state}>
                  <span className="ws-task-dot" aria-hidden="true" />
                  <span className="ws-task-tool">{TOOL_LABELS[entry.tool] || entry.tool}</span>
                  <span className="ws-task-model">{entry.model || '—'}</span>
                  <span className="ws-task-summary">{entry.summary || '（无提示词）'}</span>
                  <span className="ws-task-time"><Clock size={10} /> {entry.createdAt ? entry.createdAt.slice(11, 19) : '—'}</span>
                  <span className="ws-task-state">{entry.state === 'running' ? '生成中' : '已完成'}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
