'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Loader2, PanelRightClose, PanelRightOpen, Plus, Search, Settings, LayoutGrid } from 'lucide-react';
import BrandMark from '@/components/BrandMark';
import StudioCanvas from '@/components/studio/StudioCanvas';
import AgentPanel from './AgentPanel';
import TaskCenter from './TaskCenter';
import { createStudioProject, listStudioProjects, type StudioProjectSummary } from '@/lib/studioApi';
import { createCanvasBus } from '@/lib/canvasBus';

function formatDate(timestamp: number): string {
  if (!timestamp) return '';
  return new Date(timestamp * 1000).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

/**
 * 三栏工作台外壳：左项目栏 / 中画布 / 右 Agent。
 * 画布沿用既有 <StudioCanvas>；Agent 面板当前为占位，不发送请求。
 */
export default function WorkspaceShell({ projectId }: { projectId?: string }) {
  const router = useRouter();
  const [projects, setProjects] = useState<StudioProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [creating, setCreating] = useState(false);
  const [agentOpen, setAgentOpen] = useState(true);
  const bus = useMemo(() => createCanvasBus(), []);

  const loadProjects = useCallback(async () => {
    try {
      const next = await listStudioProjects(false);
      setProjects(next);
      setError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '项目列表暂时不可用。');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    void Promise.resolve().then(() => { if (active) return loadProjects(); });
    return () => { active = false; };
  }, [loadProjects]);

  const current = useMemo(() => projects.find(item => item.id === projectId), [projects, projectId]);
  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return projects;
    return projects.filter(item => item.title.toLowerCase().includes(keyword));
  }, [projects, query]);

  const createProject = async () => {
    setCreating(true);
    try {
      const project = await createStudioProject('未命名作品');
      router.push(`/workspace/${project.id}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '新建项目失败。');
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="ws-shell" data-agent={agentOpen ? 'open' : 'closed'}>
      <aside className="ws-rail" aria-label="项目">
        <div className="ws-rail-brand">
          <Link href="/workspace" className="ws-rail-brand-inner">
            <BrandMark size={30} />
            <span>LeonTV</span>
          </Link>
        </div>

        <button type="button" className="ws-new" onClick={() => void createProject()} disabled={creating}>
          {creating ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
          {creating ? '正在新建…' : '新建项目'}
        </button>

        <label className="ws-search">
          <Search size={14} />
          <input
            value={query}
            onChange={event => setQuery(event.target.value)}
            placeholder="搜索项目"
            aria-label="搜索项目"
          />
        </label>

        <div className="ws-rail-label">全部项目</div>
        <nav className="ws-project-list" aria-label="项目列表">
          {loading && <p className="ws-rail-note"><Loader2 size={13} className="animate-spin" /> 正在读取项目…</p>}
          {!loading && error && <p className="ws-rail-note ws-rail-error">{error}</p>}
          {!loading && !error && filtered.length === 0 && (
            <p className="ws-rail-note">{query ? '没有匹配的项目。' : '还没有项目，先新建一个。'}</p>
          )}
          {filtered.map(project => (
            <button
              key={project.id}
              type="button"
              className="ws-project"
              aria-current={project.id === projectId ? 'page' : undefined}
              onClick={() => router.push(`/workspace/${project.id}`)}
            >
              <span className="ws-project-title">{project.title}</span>
              <span className="ws-project-meta">
                {project.node_count} 节点
                {project.updated_at ? ` · ${formatDate(project.updated_at)}` : ''}
              </span>
            </button>
          ))}
        </nav>

        <div className="ws-rail-footer">
          <Link href="/studio" className="ws-rail-link"><LayoutGrid size={15} /> 项目工作台</Link>
          <Link href="/settings" className="ws-rail-link"><Settings size={15} /> 设置</Link>
        </div>
      </aside>

      <main className="ws-canvas">
        {projectId ? (
          <>
            <StudioCanvas key={projectId} projectId={projectId} variant="workspace" bus={bus} />
            <TaskCenter />
          </>
        ) : (
          <div className="ws-canvas-empty">
            <LayoutGrid size={30} />
            <h1>从左侧选择一个项目</h1>
            <p>或者新建一个空白画布，开始把故事铺开。</p>
            <button type="button" className="ws-new" onClick={() => void createProject()} disabled={creating}>
              <Plus size={15} /> 新建项目
            </button>
          </div>
        )}
      </main>

      <div className="ws-agent-column">
        <button
          type="button"
          className="ws-agent-toggle"
          onClick={() => setAgentOpen(value => !value)}
          aria-label={agentOpen ? '收起 Agent' : '展开 Agent'}
          title={agentOpen ? '收起 Agent' : '展开 Agent'}
        >
          {agentOpen ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}
        </button>
        {agentOpen && <AgentPanel projectTitle={current?.title} bus={bus} />}
      </div>
    </div>
  );
}
