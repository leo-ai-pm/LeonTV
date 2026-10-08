'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowRight, Copy, Film, FolderOpen, LayoutGrid, Plus, RotateCcw, Search, Trash2, WandSparkles, X } from 'lucide-react';
import { cloneStudioProject, createStudioProject, listStudioProjects, restoreStudioProject, saveStudioProject, trashStudioProject, type StudioProjectSummary, type StudioNode, type StudioEdge } from '@/lib/studioApi';

const DEMO_NODES: StudioNode[] = [
  { id: 'demo-idea', type: 'studio', position: { x: 80, y: 180 }, data: { kind: 'text', title: '故事灵感', description: '海边小城的邮差收到一封来自十年后的信。', status: 'completed' } },
  { id: 'demo-storyboard', type: 'studio', position: { x: 420, y: 180 }, data: { kind: 'storyboard', title: '第一镜 · 黄昏来信', description: '远景：夕阳下的海边小城。邮差从旧信箱取出一封没有邮戳的信。', status: 'completed', duration: 5 } },
  { id: 'demo-image', type: 'studio', position: { x: 760, y: 180 }, data: { kind: 'image', title: '参考图', description: '夕阳下的海边小城，旧信箱，电影感，暖色调。', status: 'idle' } },
  { id: 'demo-video', type: 'studio', position: { x: 1100, y: 180 }, data: { kind: 'video', title: '视频片段', description: '镜头从海面缓缓推向旧信箱，邮差取出信。', status: 'idle' } },
];
const DEMO_EDGES: StudioEdge[] = [
  { id: 'demo-edge-1', source: 'demo-idea', target: 'demo-storyboard', type: 'smoothstep' },
  { id: 'demo-edge-2', source: 'demo-storyboard', target: 'demo-image', type: 'smoothstep' },
  { id: 'demo-edge-3', source: 'demo-image', target: 'demo-video', type: 'smoothstep' },
];

export default function StudioProjects() {
  const router = useRouter();
  const [projects, setProjects] = useState<StudioProjectSummary[]>([]);
  const [query, setQuery] = useState('');
  const [trashed, setTrashed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<StudioProjectSummary | null>(null);
  const [error, setError] = useState('');

  const refresh = async (showLoading = true) => {
    if (showLoading) setLoading(true);
    try {
      setProjects(await listStudioProjects(trashed));
      setError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '项目列表加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); }, [trashed]);

  const visible = useMemo(() => projects.filter(project => project.title.toLowerCase().includes(query.trim().toLowerCase())), [projects, query]);

  const create = async () => {
    setBusyId('create');
    setError('');
    try {
      const project = await createStudioProject(title.trim() || '未命名作品');
      router.push(`/studio/${encodeURIComponent(project.id)}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '创建项目失败');
    } finally {
      setBusyId(null);
    }
  };

  const createDemo = async () => {
    setBusyId('demo');
    setError('');
    try {
      const project = await createStudioProject('海边来信 · 演示项目');
      await saveStudioProject(project.id, project.revision, project.title, DEMO_NODES, DEMO_EDGES);
      router.push(`/studio/${encodeURIComponent(project.id)}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '创建演示项目失败');
    } finally {
      setBusyId(null);
    }
  };

  const clone = async (id: string) => {
    setBusyId(id);
    try {
      const copy = await cloneStudioProject(id);
      router.push(`/studio/${encodeURIComponent(copy.id)}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '复制项目失败');
    } finally {
      setBusyId(null);
    }
  };

  const remove = async () => {
    if (!deleteTarget) return;
    setBusyId(deleteTarget.id);
    try {
      await trashStudioProject(deleteTarget.id);
      setDeleteTarget(null);
      await refresh(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '移入回收站失败');
    } finally {
      setBusyId(null);
    }
  };

  const restore = async (id: string) => {
    setBusyId(id);
    try {
      await restoreStudioProject(id);
      await refresh(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '恢复失败');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <main className="studio-projects min-h-screen px-8 py-8 text-[#20253a]">
      <div className="mx-auto max-w-[1420px]">
        <header className="mb-7 flex items-start justify-between">
          <div>
            <div className="mb-2 text-xs font-semibold tracking-[0.2em] text-[#7c859a]">LEONTV · CREATIVE SPACE</div>
            <h1 className="text-[30px] font-semibold tracking-tight">项目工作台</h1>
            <p className="mt-2 text-sm text-[#778097]">把故事、分镜和生成素材放在同一个桌面空间。</p>
          </div>
          <button onClick={() => { setTitle(''); setCreateOpen(true); }} className="studio-primary-button flex items-center gap-2 rounded-xl px-4 py-3 text-sm font-semibold">
            <Plus size={17} /> 新建项目
          </button>
        </header>

        <section className="studio-hero relative mb-8 overflow-hidden rounded-[24px] px-9 py-8 text-white">
          <div className="relative z-10 max-w-[650px]">
            <span className="rounded-full border border-white/20 bg-white/10 px-3 py-1 text-[11px] tracking-wide">桌面创作 · 画布预览版</span>
            <h2 className="mt-5 text-[28px] font-semibold leading-snug">灵感从这里开始，<br />在画布上长成一部作品。</h2>
            <p className="mt-3 text-sm leading-6 text-white/70">从空白画布自由编排，或打开已有六阶段项目查看创作脉络。画布编辑与保存不需要模型密钥。</p>
            <div className="mt-6 flex gap-3">
              <button onClick={() => { setTitle(''); setCreateOpen(true); }} className="flex items-center gap-2 rounded-xl bg-[#f4ae79] px-4 py-2.5 text-sm font-semibold text-[#1d2441] hover:bg-[#ffc095]">
                <LayoutGrid size={17} /> 打开空白画布
              </button>
              <button onClick={() => router.push('/')} className="flex items-center gap-2 rounded-xl border border-white/25 bg-white/10 px-4 py-2.5 text-sm text-white hover:bg-white/20">
                <WandSparkles size={17} /> 从灵感自动生成
              </button>
              <button onClick={() => void createDemo()} disabled={busyId === 'demo'} className="flex items-center gap-2 rounded-xl border border-white/25 bg-white/10 px-4 py-2.5 text-sm text-white hover:bg-white/20">
                <Film size={17} /> {busyId === 'demo' ? '创建中…' : '打开演示项目'}
              </button>
            </div>
          </div>
          <div className="studio-hero-decoration" aria-hidden="true"><div /><div /><div /></div>
        </section>

        <div className="mb-5 flex items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <button onClick={() => setTrashed(false)} className={`studio-tab ${!trashed ? 'studio-tab-active' : ''}`}>全部项目 <span>{!trashed ? projects.length : ''}</span></button>
            <button onClick={() => setTrashed(true)} className={`studio-tab ${trashed ? 'studio-tab-active' : ''}`}><Trash2 size={15} /> 回收站</button>
          </div>
          <label className="flex w-[270px] items-center gap-2 rounded-xl border border-[#e3e6ef] bg-white px-3 py-2.5 text-[#8b93a5]">
            <Search size={16} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索项目" className="w-full bg-transparent text-sm text-[#28304a] outline-none placeholder:text-[#a9b0bf]" />
          </label>
        </div>

        {error && <div role="alert" className="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
        {loading ? (
          <div className="rounded-2xl border border-[#e5e8ef] bg-white px-6 py-12 text-center text-sm text-[#8b93a5]">正在加载项目…</div>
        ) : visible.length === 0 ? (
          <div className="flex min-h-72 flex-col items-center justify-center rounded-2xl border border-dashed border-[#d7dce7] bg-white/70 text-center">
            <FolderOpen size={34} className="text-[#b3bcce]" />
            <div className="mt-4 font-medium">{query ? '没有匹配的项目' : trashed ? '回收站是空的' : '还没有项目'}</div>
            {!query && !trashed && <p className="mt-2 text-sm text-[#929bae]">创建一张画布，或者从创作首页开始。</p>}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4 xl:grid-cols-3">
            {visible.map((project, index) => (
              <article key={project.id} className="studio-project-card group overflow-hidden rounded-2xl border border-[#e6e9f0] bg-white transition hover:-translate-y-0.5 hover:shadow-[0_16px_32px_rgba(30,40,70,.08)]">
                <button onClick={() => !trashed && router.push(`/studio/${encodeURIComponent(project.id)}`)} disabled={trashed} className="block w-full text-left disabled:cursor-default">
                  <div className={`studio-card-art studio-card-art-${index % 4}`}>
                    <div className="studio-art-frame"><Film size={29} strokeWidth={1.5} /></div>
                    <span className="studio-card-kind">{project.kind === 'workflow' ? '六阶段项目' : '自由画布'}</span>
                  </div>
                  <div className="px-5 pt-4">
                    <div className="flex items-center justify-between gap-2"><h3 className="truncate font-semibold">{project.title}</h3>{!trashed && <ArrowRight size={17} className="shrink-0 text-[#b3bac8] group-hover:text-[#2c3654]" />}</div>
                    <p className="mt-1.5 text-xs text-[#949caf]">{project.node_count ? `${project.node_count} 个画布节点` : project.kind === 'workflow' ? '打开后生成画布视图' : '空白画布'} · {project.updated_at ? new Date(project.updated_at * 1000).toLocaleDateString('zh-CN') : '刚刚'}</p>
                  </div>
                </button>
                <div className="mt-4 flex justify-end border-t border-[#f0f1f5] px-5 py-3">
                  {trashed ? (
                    <button onClick={() => restore(project.id)} disabled={busyId === project.id} className="flex items-center gap-1.5 text-xs font-medium text-[#4d648f] hover:text-[#1d2441]"><RotateCcw size={14} /> 恢复项目</button>
                  ) : (
                    <div className="flex gap-4"><button onClick={() => void clone(project.id)} disabled={busyId === project.id} className="flex items-center gap-1.5 text-xs text-[#758199] hover:text-[#2c3654]"><Copy size={14} /> 复制</button><button onClick={() => setDeleteTarget(project)} className="flex items-center gap-1.5 text-xs text-[#98a0af] hover:text-red-500"><Trash2 size={14} /> 移入回收站</button></div>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </div>

      {createOpen && <div className="studio-modal-backdrop" onMouseDown={() => setCreateOpen(false)}><form onSubmit={event => { event.preventDefault(); void create(); }} onMouseDown={event => event.stopPropagation()} className="studio-modal">
        <div className="mb-5 flex items-center justify-between"><h2 className="text-lg font-semibold">新建画布项目</h2><button type="button" onClick={() => setCreateOpen(false)} aria-label="关闭"><X size={20} /></button></div>
        <label className="mb-2 block text-sm text-[#69738a]">项目名称</label>
        <input autoFocus value={title} onChange={event => setTitle(event.target.value)} maxLength={120} placeholder="例如：海边的最后一封信" className="w-full rounded-xl border border-[#dfe3ed] px-4 py-3 text-sm outline-none focus:border-[#48597d]" />
        <p className="mt-3 text-xs text-[#9aa3b5]">创建后可在画布里加入文本、参考图、视频和分镜节点。</p>
        <div className="mt-7 flex justify-end gap-2"><button type="button" onClick={() => setCreateOpen(false)} className="rounded-xl px-4 py-2.5 text-sm text-[#69738a]">取消</button><button type="submit" disabled={busyId === 'create'} className="studio-primary-button rounded-xl px-5 py-2.5 text-sm font-semibold">{busyId === 'create' ? '创建中…' : '创建并打开'}</button></div>
      </form></div>}

      {deleteTarget && <div className="studio-modal-backdrop" onMouseDown={() => setDeleteTarget(null)}><div onMouseDown={event => event.stopPropagation()} className="studio-modal">
        <div className="mb-2 flex items-center gap-2"><Trash2 size={19} className="text-[#a95b59]" /><h2 className="text-lg font-semibold">移入回收站</h2></div>
        <p className="text-sm leading-6 text-[#69738a]">「{deleteTarget.title}」将从项目列表移走，之后可以在回收站恢复。原有生成素材不会在此操作中删除。</p>
        <div className="mt-7 flex justify-end gap-2"><button onClick={() => setDeleteTarget(null)} className="rounded-xl px-4 py-2.5 text-sm text-[#69738a]">取消</button><button onClick={() => void remove()} disabled={busyId === deleteTarget.id} className="rounded-xl bg-[#9e4f50] px-4 py-2.5 text-sm font-semibold text-white">确认移入</button></div>
      </div></div>}
    </main>
  );
}
