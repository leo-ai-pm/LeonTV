'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  ReactFlow, Background, Controls, Handle, MiniMap, Position, addEdge, useEdgesState, useNodesState,
  type Connection, type NodeProps,
} from '@xyflow/react';
import { ArrowLeft, ArrowUpRight, Check, ChevronDown, CircleAlert, Clapperboard, Download, FileText, Film, Image as ImageIcon, LayoutGrid, Loader2, Play, Plus, Redo2, Save, Sparkles, Type, Undo2, Upload, Video, X } from 'lucide-react';
import { fetchModelGroupsByType } from '@/lib/modelRegistry';
import { exportStudioProject, getStudioProject, mediaUrl, saveStudioProject, type StudioEdge, type StudioKind, type StudioNode, type StudioNodeData, type StudioProject } from '@/lib/studioApi';
import ModelOptions from '@/components/ModelOptions';
import { describeImportedMedia, importKindForName } from '@/lib/studioImport';
import { planModels } from '@/lib/modelRouter';
import type { CanvasBus, CanvasHandlers } from '@/lib/canvasBus';
import { uploadMedia } from '@/lib/workflowApi';
import HarnessRunPanel from '@/components/harness/HarnessRunPanel';
import type { ProviderGroup } from '@/config/models';

const KIND_LABEL: Record<StudioKind, string> = {
  text: '文本', script: '剧本', character: '角色', scene: '场景', storyboard: '分镜',
  image: '参考图', video: '视频片段', output: '成片',
};

const KIND_COLOR: Record<StudioKind, string> = {
  text: '#697d9e', script: '#6672a7', character: '#aa7a91', scene: '#87986f',
  storyboard: '#b1836c', image: '#548b9c', video: '#8a6aab', output: '#c39764',
};

const KIND_ICON: Record<StudioKind, typeof FileText> = {
  text: Type, script: FileText, character: Sparkles, scene: LayoutGrid,
  storyboard: Clapperboard, image: ImageIcon, video: Video, output: Film,
};

function StudioNodeCard({ data, selected }: NodeProps<StudioNode>) {
  const kind = data.kind || 'text';
  const Icon = KIND_ICON[kind];
  const src = mediaUrl(data.mediaPath);
  return (
    <div className={`studio-node ${selected ? 'studio-node-selected' : ''}`}>
      <Handle type="target" position={Position.Left} className="studio-node-handle" />
      <div className="studio-node-top"><span className="studio-node-icon" style={{ backgroundColor: `${KIND_COLOR[kind]}20`, color: KIND_COLOR[kind] }}><Icon size={16} /></span><span>{KIND_LABEL[kind]}</span><span className={`studio-node-status studio-status-${data.status || 'idle'}`} /></div>
      <div className="studio-node-title">{data.title || '未命名节点'}</div>
      {src && kind === 'image' ? <img src={src} alt={data.title} className="studio-node-preview" /> : src && (kind === 'video' || kind === 'output') ? <div className="studio-node-video"><Play size={25} fill="currentColor" /></div> : <div className="studio-node-desc">{data.description || (kind === 'image' || kind === 'video' ? '在右侧填写提示词或连接上游节点' : '点击查看或编辑内容')}</div>}
      <div className="studio-node-footer">{data.sourceStage ? '六阶段产物' : data.status === 'running' ? '生成中' : data.mediaPath ? '已有素材' : '自由节点'}{data.versions?.length ? ` · ${data.versions.length} 个版本` : ''}</div>
      <Handle type="source" position={Position.Right} className="studio-node-handle" />
    </div>
  );
}

const nodeTypes = { studio: StudioNodeCard };
type CanvasSnapshot = { nodes: StudioNode[]; edges: StudioEdge[] };

function descendants(nodeId: string, edges: StudioEdge[]): Set<string> {
  const found = new Set<string>();
  const queue = [nodeId];
  while (queue.length) {
    const current = queue.shift();
    for (const edge of edges) {
      if (edge.source === current && !found.has(edge.target)) {
        found.add(edge.target);
        queue.push(edge.target);
      }
    }
  }
  return found;
}

function canConnect(connection: Pick<Connection, 'source' | 'target'>, nodes: StudioNode[], edges: StudioEdge[]): boolean {
  if (!connection.source || !connection.target || connection.source === connection.target) return false;
  if (edges.some(edge => edge.source === connection.source && edge.target === connection.target)) return false;
  if (descendants(connection.target, edges).has(connection.source)) return false;
  const source = nodes.find(node => node.id === connection.source)?.data.kind;
  const target = nodes.find(node => node.id === connection.target)?.data.kind;
  if (!source || !target) return false;
  const allowed: Record<StudioKind, StudioKind[]> = {
    text: ['script', 'storyboard', 'image', 'video'], script: ['character', 'scene', 'storyboard', 'image', 'video'],
    character: ['storyboard', 'image', 'video'], scene: ['storyboard', 'image', 'video'],
    storyboard: ['image', 'video'], image: ['image', 'video'], video: ['output'], output: [],
  };
  return allowed[source].includes(target);
}

function newNode(kind: StudioKind, index: number, model = ''): StudioNode {
  return {
    id: `node-${crypto.randomUUID()}`,
    type: 'studio',
    position: { x: 100 + (index % 4) * 340, y: 100 + Math.floor(index / 4) * 210 },
    data: { kind, title: KIND_LABEL[kind], description: '', status: 'idle', model },
  };
}

export default function StudioCanvas({ projectId, variant = 'standalone', bus }: {
  projectId: string;
  /** workspace: 放进三栏工作台时只渲染画布本身，隐藏自带工具箱与属性面板。 */
  variant?: 'standalone' | 'workspace';
  /** 工作台把命令总线传进来，让右侧 Agent 面板也能操作画布。 */
  bus?: CanvasBus;
}) {
  const router = useRouter();
  const [nodes, setNodes, baseOnNodesChange] = useNodesState<StudioNode>([]);
  const [edges, setEdges, baseOnEdgesChange] = useEdgesState<StudioEdge>([]);
  const [title, setTitle] = useState('');
  const [sourceSessionId, setSourceSessionId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [saveState, setSaveState] = useState<'saved' | 'unsaved' | 'saving' | 'error'>('saved');
  const [saveError, setSaveError] = useState('');
  const [editSeq, setEditSeq] = useState(0);
  const [notice, setNotice] = useState('');
  const [exports, setExports] = useState<NonNullable<StudioProject['exports']>>([]);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const [modelGroups, setModelGroups] = useState<Record<'t2i' | 'i2i' | 'video', ProviderGroup[]>>({ t2i: [], i2i: [], video: [] });
  const [defaultModels, setDefaultModels] = useState({ image: '', imageEdit: '', video: '' });
  const [harnessOpen, setHarnessOpen] = useState(false);
  const nodeRunLocks = useRef(new Set<string>());
  const revisionRef = useRef(0);
  const editSeqRef = useRef(0);
  const savedSeqRef = useRef(0);
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());
  const latestRef = useRef({ title, nodes, edges });
  const historyRef = useRef<{ past: CanvasSnapshot[]; future: CanvasSnapshot[] }>({ past: [], future: [] });
  const [historyCounts, setHistoryCounts] = useState({ past: 0, future: 0 });

  useEffect(() => { latestRef.current = { title, nodes, edges }; }, [title, nodes, edges]);

  const markDirty = useCallback(() => {
    editSeqRef.current += 1;
    setEditSeq(editSeqRef.current);
    setSaveState('unsaved');
  }, []);

  useEffect(() => {
    let active = true;
    getStudioProject(projectId).then(project => {
      if (!active) return;
      if (project.trashed) throw new Error('这个项目已在回收站，请先恢复');
      setTitle(project.title);
      setNodes(project.nodes);
      setEdges(project.edges);
      setSourceSessionId(project.source_session_id);
      setExports(project.exports || []);
      historyRef.current = { past: [], future: [] };
      setHistoryCounts({ past: 0, future: 0 });
      revisionRef.current = project.revision;
      editSeqRef.current = 0;
      savedSeqRef.current = 0;
      setSaveState('saved');
    }).catch(cause => { if (active) setLoadError(cause instanceof Error ? cause.message : '项目加载失败'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [projectId, setNodes, setEdges]);

  useEffect(() => {
    // 默认模型按「已配置密钥」选，而不是照搬全局配置——否则会选中没有 key 的厂商。
    Promise.all([
      fetchModelGroupsByType('t2i'),
      fetchModelGroupsByType('i2i'),
      fetchModelGroupsByType('video'),
      fetch('/api/config').then(response => response.json()).catch(() => null),
    ]).then(([t2i, i2i, video, payload]) => {
      setModelGroups({ t2i, i2i, video });
      const models = payload?.config?.models || {};
      const credentials = payload?.config?.api_providers || {};
      const plan = planModels({ llm: [], vlm: [], t2i, i2i, video }, credentials);
      setDefaultModels({
        image: plan.picks.t2i?.model || models.image_t2i || '',
        imageEdit: plan.picks.i2i?.model || models.image_it2i || '',
        video: plan.picks.video?.model || models.video_first_frame || models.video || '',
      });
    }).catch(() => {});
  }, []);

  const queueSave = useCallback(() => {
    saveChainRef.current = saveChainRef.current.catch(() => {}).then(async () => {
      const seq = editSeqRef.current;
      if (seq === savedSeqRef.current) return;
      setSaveState('saving');
      const current = latestRef.current;
      try {
        const saved = await saveStudioProject(projectId, revisionRef.current, current.title.trim() || '未命名作品', current.nodes, current.edges);
        revisionRef.current = saved.revision;
        savedSeqRef.current = seq;
        setSaveError('');
        setSaveState(editSeqRef.current === seq ? 'saved' : 'unsaved');
      } catch (cause) {
        setSaveError(cause instanceof Error ? cause.message : '保存失败');
        setSaveState('error');
      }
    });
  }, [projectId]);

  useEffect(() => {
    if (editSeq === 0) return;
    const timer = window.setTimeout(queueSave, 750);
    return () => window.clearTimeout(timer);
  }, [editSeq, queueSave]);

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (editSeqRef.current !== savedSeqRef.current) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, []);

  const leaveCanvas = async (href: string) => {
    queueSave();
    await saveChainRef.current;
    if (editSeqRef.current !== savedSeqRef.current) { setNotice('保存未完成，请保存成功后再离开。'); return; }
    router.push(href);
  };

  const selectedNode = useMemo(() => nodes.find(node => node.id === selectedId) || null, [nodes, selectedId]);
  const selectedHasImageInput = Boolean(selectedNode && edges.some(edge => edge.target === selectedNode.id && nodes.some(node => node.id === edge.source && node.data.kind === 'image')));
  const selectedModelId = selectedNode?.data.model || (selectedNode?.data.kind === 'video' ? defaultModels.video : selectedHasImageInput ? defaultModels.imageEdit : defaultModels.image);
  const selectedModelGroups = modelGroups[selectedNode?.data.kind === 'video' ? 'video' : selectedHasImageInput ? 'i2i' : 't2i'];

  const videoNodes = useMemo(() => nodes.filter(node => node.data.kind === 'video'), [nodes]);
  const readyVideoCount = videoNodes.filter(node => node.data.mediaPath && node.data.status !== 'stale').length;

  const exportVideo = async () => {
    setExporting(true);
    setExportError('');
    try {
      queueSave();
      await saveChainRef.current;
      if (editSeqRef.current !== savedSeqRef.current) throw new Error('画布保存失败，请先解决保存问题后重试。');
      const project = await exportStudioProject(projectId, revisionRef.current);
      revisionRef.current = project.revision;
      setExports(project.exports || []);
      setNotice('成片已导出，可在左侧预览和下载。');
    } catch (cause) {
      setExportError(cause instanceof Error ? cause.message : '成片导出失败');
    } finally {
      setExporting(false);
    }
  };

  const captureHistory = useCallback(() => {
    const current = latestRef.current;
    const snapshot: CanvasSnapshot = { nodes: structuredClone(current.nodes), edges: structuredClone(current.edges) };
    const history = historyRef.current;
    if (history.past.length && JSON.stringify(history.past.at(-1)) === JSON.stringify(snapshot)) return;
    history.past = [...history.past, snapshot].slice(-20);
    history.future = [];
    setHistoryCounts({ past: history.past.length, future: 0 });
  }, []);

  const travelHistory = useCallback((direction: 'undo' | 'redo') => {
    const history = historyRef.current;
    const from = direction === 'undo' ? history.past : history.future;
    const to = direction === 'undo' ? history.future : history.past;
    const snapshot = from.pop();
    if (!snapshot) return;
    const current = latestRef.current;
    to.push({ nodes: structuredClone(current.nodes), edges: structuredClone(current.edges) });
    if (to.length > 20) to.shift();
    const next = structuredClone(snapshot);
    latestRef.current = { ...current, ...next };
    setNodes(next.nodes);
    setEdges(next.edges);
    setSelectedId(null);
    setHistoryCounts({ past: history.past.length, future: history.future.length });
    markDirty();
  }, [markDirty, setNodes, setEdges]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (!((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z')) return;
      if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
      event.preventDefault();
      travelHistory(event.shiftKey ? 'redo' : 'undo');
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [travelHistory]);

  const updateNode = useCallback((nodeId: string, patch: Partial<StudioNodeData>, affectsDownstream = false) => {
    const stale = affectsDownstream ? descendants(nodeId, edges) : new Set<string>();
    const nextNodes = latestRef.current.nodes.map(node => node.id === nodeId
      ? { ...node, data: { ...node.data, ...patch } }
      : stale.has(node.id) && node.data.status !== 'running'
        ? { ...node, data: { ...node.data, status: 'stale' } }
        : node);
    latestRef.current = { ...latestRef.current, nodes: nextNodes };
    setNodes(nextNodes);
    markDirty();
  }, [edges, setNodes, markDirty]);

  // ── 断线自动续查：只查询原任务，永不重新提交 ──
  const AUTO_RESUME_DELAYS = [15000, 45000, 120000];
  const autoResumeRef = useRef(new Map<string, { attempts: number; timer: number; exhausted?: boolean }>());

  const scheduleAutoResume = (nodeId: string, jobId: string) => {
    const existing = autoResumeRef.current.get(nodeId);
    if (existing?.exhausted || existing?.timer) return;
    const attempts = (existing?.attempts || 0) + 1;
    if (attempts > AUTO_RESUME_DELAYS.length) {
      autoResumeRef.current.set(nodeId, { attempts, timer: 0, exhausted: true });
      setNotice('自动续查已用尽，请手动点「生成」继续查询原任务。');
      return;
    }
    const delay = AUTO_RESUME_DELAYS[attempts - 1];
    const timer = window.setTimeout(() => {
      const entry = autoResumeRef.current.get(nodeId);
      if (entry) entry.timer = 0;
      const current = latestRef.current.nodes.find(item => item.id === nodeId);
      if (!current || current.data.jobId !== jobId || current.data.status !== 'interrupted') return;
      setNotice(`连接中断，正在自动续查第 ${attempts}/${AUTO_RESUME_DELAYS.length} 次…`);
      void fetch(`/api/sandbox/jobs/${jobId}/resume`, { method: 'POST' })
        .then(response => {
          if (!response.ok) throw new Error('resume rejected');
          updateNode(nodeId, { status: 'running', error: '' });
        })
        .catch(() => scheduleAutoResume(nodeId, jobId));
    }, delay);
    autoResumeRef.current.set(nodeId, { attempts, timer });
  };

  useEffect(() => {
    let active = true;
    let polling = false;
    const pollJobs = async () => {
      if (polling) return;
      polling = true;
      // 已中断但保留了任务身份：自动续查（每个节点有次数上限）。
      latestRef.current.nodes
        .filter(node => node.data.status === 'interrupted' && node.data.jobId)
        .forEach(node => scheduleAutoResume(node.id, node.data.jobId as string));
      try {
        await Promise.all(latestRef.current.nodes.filter(node => node.data.status === 'running' && node.data.jobId).map(async node => {
          try {
            const response = await fetch(`/api/sandbox/jobs/${node.data.jobId}`);
            if (response.status === 404 && active) {
              updateNode(node.id, { status: 'interrupted', error: '暂未查到原提交记录，请先查询提交状态，避免重复生成。' });
              return;
            }
            if (!response.ok) return;
            const job = await response.json();
            if (!active || ['running', 'pending', 'queued'].includes(job.status)) return;
            const currentJob = latestRef.current.nodes.find(item => item.id === node.id);
            if (currentJob?.data.jobId !== node.data.jobId) return;
            const output = job.output || {};
            const path = node.data.kind === 'image' ? output.result?.[0] : output.video_path;
            if (job.status === 'completed' && path) {
              const current = latestRef.current.nodes.find(item => item.id === node.id);
              const versions = Array.from(new Set([...(current?.data.versions || []), path]));
              autoResumeRef.current.delete(node.id);
              // 生成完成后去掉名称里的「待生成」后缀。
              const previousTitle = current?.data.title || '';
              const settledTitle = previousTitle.replace(/\s*·\s*待生成\s*$/, '');
              updateNode(node.id, { mediaPath: path, versions, status: 'completed', error: '', jobId: '',
                ...(settledTitle && settledTitle !== previousTitle ? { title: settledTitle } : {}) }, true);
              setNotice('已生成新版本，并保留旧版本。');
            } else {
              const interrupted = job.status === 'interrupted' || output.query_pending;
              updateNode(node.id, { status: interrupted ? 'interrupted' : 'error',
                error: output.error || '生成任务未返回素材。',
                providerTaskId: output.provider_task?.task_id || '',
                jobId: interrupted ? node.data.jobId : '' });
              if (interrupted && node.data.jobId) scheduleAutoResume(node.id, node.data.jobId);
            }
          } catch { /* A connection failure does not mean the provider task failed. */ }
        }));
      } finally { polling = false; }
    };
    void pollJobs();
    const timer = window.setInterval(() => void pollJobs(), 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [updateNode]);

  const addNode = (kind: StudioKind, patch?: Partial<StudioNodeData>) => {
    captureHistory();
    const model = kind === 'image' ? defaultModels.image : kind === 'video' ? defaultModels.video : '';
    const node = newNode(kind, nodes.length, model);
    if (patch) node.data = { ...node.data, ...patch };
    setNodes(current => [...current.map(item => ({ ...item, selected: false })), { ...node, selected: true }]);
    setSelectedId(node.id);
    markDirty();
  };

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState('');

  // 导入已有素材：只上传并放置节点，不调用模型、不产生费用。
  const importMedia = async (file: File) => {
    setImportError('');
    if (!importKindForName(file.name)) {
      setImportError('只支持图片或视频文件。');
      return;
    }
    setImporting(true);
    try {
      const uploaded = await uploadMedia(file);
      const described = describeImportedMedia(file.name, uploaded.file_path);
      if (!described) throw new Error('上传结果不在可引用的素材目录中。');
      captureHistory();
      // 选中同类型空节点时直接填入，避免留下无法导出的占位节点。
      const target = nodes.find(item => item.id === selectedId
        && item.data.kind === described.kind && !item.data.mediaPath);
      if (target) {
        updateNode(target.id, { title: described.title, mediaPath: described.mediaPath,
          versions: [described.mediaPath], status: 'completed', error: '' });
        setNotice(`已导入素材「${described.title}」，填入选中的空节点。`);
        return;
      }
      const node = newNode(described.kind, nodes.length);
      node.data = { ...node.data, title: described.title, mediaPath: described.mediaPath,
        versions: [described.mediaPath], status: 'completed' };
      setNodes(current => [...current.map(item => ({ ...item, selected: false })), { ...node, selected: true }]);
      setSelectedId(node.id);
      markDirty();
      setNotice(`已导入素材「${described.title}」。`);
    } catch (cause) {
      setImportError(cause instanceof Error ? cause.message : '导入失败，请重试。');
    } finally {
      setImporting(false);
    }
  };

  const validateConnection = useCallback((connection: Connection | StudioEdge) => canConnect(connection, nodes, edges), [nodes, edges]);

  const runNode = async (node: StudioNode) => {
    if (nodeRunLocks.current.has(node.id)) return;
    nodeRunLocks.current.add(node.id);
    try {
    const kind = node.data.kind;
    if (node.data.sourceStage) { setNotice('六阶段产物请在原流程页继续生成；画布目前用于查看与编排。'); return; }
    if (kind !== 'image' && kind !== 'video') return;
    if (node.data.jobId) {
      try {
        const existing = await fetch(`/api/sandbox/jobs/${node.data.jobId}`);
        if (!existing.ok) throw new Error('暂未查到原任务记录，请先核查提交结果，避免重复生成。');
        const original = await existing.json();
        if (original.status === 'interrupted') {
          const resumed = await fetch(`/api/sandbox/jobs/${node.data.jobId}/resume`, { method: 'POST' });
          const payload = await resumed.json().catch(() => ({}));
          if (!resumed.ok) throw new Error(payload.detail || '原任务暂时无法恢复查询。');
        }
        autoResumeRef.current.delete(node.id);
        updateNode(node.id, { status: 'running', error: '' });
        setNotice('正在查询原任务结果；未提交新的生成任务。');
      } catch (cause) {
        setNotice(cause instanceof Error ? cause.message : '原任务查询失败，请稍后再查。');
      }
      return;
    }
    const prompt = (node.data.description || '').trim();
    if (!prompt) { setNotice('请先在右侧填写生成提示词。'); return; }
    // 用最新快照判断上游：一键出片会在等待期间更新节点，闭包里的旧值会误判。
    const liveNodes = latestRef.current.nodes || [];
    const liveEdges = latestRef.current.edges || [];
    const hasImageInput = liveEdges.some(edge => edge.target === node.id && liveNodes.some(source => source.id === edge.source && source.data.kind === 'image'));
    const model = node.data.model || (kind === 'image' ? (hasImageInput ? defaultModels.imageEdit : defaultModels.image) : defaultModels.video);
    if (!model) { setNotice('当前没有可用模型，请先前往设置配置模型。'); return; }
    const parent = liveEdges.map(edge => edge.target === node.id ? liveNodes.find(item => item.id === edge.source) : null)
      .find(item => item?.data.mediaPath && item.data.kind === 'image');
    const image = parent?.data.mediaPath || '';
    if (hasImageInput && (!image || parent?.data.status === 'stale')) { setNotice('上游参考图尚未就绪，请先完成或更新参考图。'); return; }
    setNotice('');
    let submittedJobId = '';
    let rejected = false;
    try {
      const provider = modelGroups[kind === 'image' ? (hasImageInput ? 'i2i' : 't2i') : 'video']
        .find(group => group.models.some(option => option.id === model))?.provider;
      if (!provider || provider === 'unknown') throw new Error('所选模型不在当前可用列表中，请重新选择模型。');
      const configResponse = await fetch('/api/config');
      if (!configResponse.ok) throw new Error('无法读取模型配置，请稍后重试。');
      const currentConfig = (await configResponse.json()).config;
      const credentials = currentConfig?.api_providers?.[provider];
      const configured = provider === 'kling'
        ? Boolean(credentials?.access_key && credentials?.secret_key)
        : Boolean(credentials?.api_key);
      if (!configured) throw new Error(`尚未配置 ${provider} 的模型密钥。请前往设置填写后重试。`);
      const ratio = [node.data.ratio, node.data.video_ratio, currentConfig?.generation?.video_ratio]
        .find(value => typeof value === 'string' && value.trim())?.trim() || '16:9';
      const duration = kind === 'video' ? node.data.duration : undefined;
      if (duration != null && (!Number.isInteger(duration) || duration < 1 || duration > 120)) {
        throw new Error('视频时长需为 1–120 秒的整数，请调整节点时长后重试。');
      }
      const jobId = crypto.randomUUID().replaceAll('-', '');
      updateNode(node.id, { status: 'submitting', error: '', jobId });
      queueSave();
      await saveChainRef.current;
      if (editSeqRef.current !== savedSeqRef.current) throw new Error('请先解决画布保存问题，再运行节点。');
      if (!latestRef.current.nodes.some(item => item.id === node.id)) return;
      submittedJobId = jobId;
      const endpoint = kind === 'video' ? 'video' : image ? 'i2i' : 't2i';
      const response = await fetch('/api/sandbox/jobs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: jobId, tool: endpoint, prompt, model, ...(image ? { image } : {}), ratio,
          ...(duration != null ? { duration } : {}) }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        rejected = response.status >= 400 && response.status < 500 && response.status !== 408;
        throw new Error(payload.error || payload.detail || `任务提交失败 (${response.status})`);
      }
      updateNode(node.id, { status: 'running', error: '' });
      setNotice('任务已提交，生成结果会自动更新；重新打开画布可以继续查询。');
    } catch (cause) {
      const uncertain = Boolean(submittedJobId && !rejected);
      updateNode(node.id, { status: uncertain ? 'interrupted' : 'error',
        jobId: uncertain ? submittedJobId : '',
        error: uncertain ? '提交结果尚未确认，请查询原任务状态，避免重复生成。' : cause instanceof Error ? cause.message : '生成失败' });
    }
    } finally { nodeRunLocks.current.delete(node.id); }
  };

  // ── 一键出片：按画布顺序逐镜生成素材，全部完成后再导出 ──
  const pipelineRunningRef = useRef(false);

  const waitForNode = (id: string, timeoutMs = 15 * 60 * 1000) =>
    new Promise<string>(resolve => {
      const started = Date.now();
      const timer = window.setInterval(() => {
        const current = latestRef.current.nodes.find(item => item.id === id);
        const status = current?.data.status || '';
        if (status === 'completed' || status === 'error' || status === 'interrupted') {
          window.clearInterval(timer);
          resolve(status);
        } else if (Date.now() - started > timeoutMs) {
          window.clearInterval(timer);
          resolve('timeout');
        }
      }, 2000);
    });

  const runPipeline = async (scope: 'single' | 'all') => {
    if (pipelineRunningRef.current) return;
    const kindRank = (kind: string) => (kind === 'image' ? 0 : kind === 'video' ? 1 : 2);
    const targets = [...latestRef.current.nodes]
      .filter(item => (item.data.kind === 'image' || item.data.kind === 'video')
        && !item.data.mediaPath && (item.data.description || '').trim())
      .sort((a, b) => (a.position.y - b.position.y) || (kindRank(a.data.kind) - kindRank(b.data.kind)));
    // 单镜 = 画布中最靠上的那一组素材节点（同一镜的图与视频 y 相同）。
    const firstY = targets[0]?.position.y;
    const list = scope === 'single' ? targets.filter(item => item.position.y === firstY) : targets;
    if (!list.length) { setNotice('没有待生成的素材节点：请先填写提示词，或已有素材。'); return; }

    pipelineRunningRef.current = true;
    try {
      for (const [index, node] of list.entries()) {
        setNotice(`一键出片：正在生成第 ${index + 1}/${list.length} 个节点「${node.data.title || node.id}」…`);
        await runNode(node);
        const status = await waitForNode(node.id);
        if (status !== 'completed') {
          setNotice(`「${node.data.title || node.id}」未完成（${status}），已停止后续生成；已生成的素材保留。`);
          return;
        }
      }
      if (scope === 'all') {
        setNotice('素材已全部生成，正在导出成片…');
        await exportVideo();
        setNotice('一键出片完成：成片已导出，可在左侧预览和下载。');
      } else {
        setNotice('单镜素材已生成；确认无误后可再执行完整出片。');
      }
    } finally {
      pipelineRunningRef.current = false;
    }
  };

  // ── 对外命令总线：注册画布动作并广播选中节点（须在任何 early return 之前）──
  const removeNode = (id: string) => {
    captureHistory();
    setNodes(current => current.filter(item => item.id !== id));
    setEdges(current => current.filter(edge => edge.source !== id && edge.target !== id));
    setSelectedId(null);
    markDirty();
  };
  const busActionsRef = useRef<CanvasHandlers | null>(null);
  useEffect(() => {
    busActionsRef.current = {
      addNode,
      updateNode: (id, patch) => updateNode(id, patch),
      deleteNode: id => removeNode(id),
      runNode: id => {
        const node = nodes.find(item => item.id === id);
        if (node) void runNode(node);
      },
      exportVideo: () => void exportVideo(),
      runPipeline: scope => { void runPipeline(scope); },
      snapshot: () => ({
        title,
        nodes: nodes.map(item => ({
          id: item.id,
          kind: item.data.kind,
          title: item.data.title || '',
          description: item.data.description || '',
          status: item.data.status || '',
          mediaPath: item.data.mediaPath || '',
          duration: item.data.duration,
        })),
      }),
    };
  });
  useEffect(() => {
    if (!bus) return;
    return bus.register({
      addNode: (kind, patch) => busActionsRef.current?.addNode(kind, patch),
      updateNode: (id, patch) => busActionsRef.current?.updateNode(id, patch),
      deleteNode: id => busActionsRef.current?.deleteNode(id),
      runNode: id => busActionsRef.current?.runNode(id),
      exportVideo: () => busActionsRef.current?.exportVideo(),
      runPipeline: scope => busActionsRef.current?.runPipeline(scope),
      snapshot: () => busActionsRef.current?.snapshot() ?? { title: '', nodes: [] },
    });
  }, [bus]);
  useEffect(() => { bus?.setSelection(selectedNode); }, [bus, selectedNode]);

  if (loading) return <div className="studio-canvas-loading"><Loader2 size={24} className="animate-spin" /> 正在打开项目画布…</div>;
  if (loadError) return <div className="studio-canvas-loading"><CircleAlert size={23} /> {loadError}<button onClick={() => router.push('/studio')}>返回项目</button></div>;


  return (
    <div className="studio-editor" data-variant={variant}>
      {harnessOpen && <HarnessRunPanel key={projectId} projectId={projectId} onClose={() => setHarnessOpen(false)} />}
      <header className="studio-editor-header">
        <button aria-label="返回项目" onClick={() => void leaveCanvas('/studio')} className="studio-icon-button"><ArrowLeft size={18} /></button>
        <div className="studio-header-divider" />
        <div className="flex min-w-0 flex-col"><input aria-label="项目名称" value={title} onChange={event => { setTitle(event.target.value); markDirty(); }} maxLength={120} className="studio-title-input" /><span className="text-[11px] text-[#939caf]">{sourceSessionId ? '六阶段项目 · 可视画布' : '自由画布 · 桌面工作台'}</span></div>
        <div className="ml-auto flex items-center gap-3 text-xs text-[#858fa4]">
          <button onClick={() => travelHistory('undo')} disabled={!historyCounts.past} title="撤销画布结构操作（⌘/Ctrl+Z）" aria-label="撤销" className="studio-icon-button disabled:opacity-35"><Undo2 size={17} /></button>
          <button onClick={() => travelHistory('redo')} disabled={!historyCounts.future} title="重做画布结构操作（⌘/Ctrl+Shift+Z）" aria-label="重做" className="studio-icon-button disabled:opacity-35"><Redo2 size={17} /></button>
          {saveState === 'saving' ? <><Loader2 size={14} className="animate-spin" /> 保存中</> : saveState === 'saved' ? <><Check size={15} className="text-[#3a9c88]" /> 已保存</> : saveState === 'error' ? <span className="text-red-600">{saveError}</span> : '待保存'}
          <button onClick={queueSave} className="studio-save-button"><Save size={15} /> 保存</button>
          <button type="button" onClick={() => setHarnessOpen(true)} className="studio-outline-button">运行验证 <span className="harness-entry-tag">测试</span></button>
          {sourceSessionId && <button onClick={() => void leaveCanvas(`/?session=${encodeURIComponent(sourceSessionId)}`)} className="studio-outline-button">阶段视图 <ArrowUpRight size={15} /></button>}
        </div>
      </header>

      <div className="studio-editor-main">
        {variant === 'standalone' && <aside className="studio-toolbox">
          <div className="studio-panel-label">创作节点</div>
          <p className="studio-toolbox-hint">点击添加到画布，拖动节点右侧连线至下一步。</p>
          {(['text', 'storyboard', 'image', 'video'] as StudioKind[]).map(kind => {
            const Icon = KIND_ICON[kind];
            return <button key={kind} onClick={() => addNode(kind)} className="studio-tool-button"><span style={{ color: KIND_COLOR[kind], backgroundColor: `${KIND_COLOR[kind]}1a` }}><Icon size={18} /></span><span>{KIND_LABEL[kind]}</span><Plus size={14} className="ml-auto text-[#aab3c3]" /></button>;
          })}
          <button type="button" onClick={() => fileInputRef.current?.click()} disabled={importing} className="studio-tool-button">
            <span style={{ color: KIND_COLOR.image, backgroundColor: `${KIND_COLOR.image}1a` }}>{importing ? <Loader2 size={18} className="animate-spin" /> : <Upload size={18} />}</span>
            <span>{importing ? '正在导入…' : '导入素材'}</span>
            <Plus size={14} className="ml-auto text-[#aab3c3]" />
          </button>
          <input ref={fileInputRef} type="file" accept="image/*,video/*" className="hidden" onChange={event => {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (file) void importMedia(file);
          }} />
          {importError && <p role="alert" className="mt-2 text-xs leading-5 text-red-600">{importError}</p>}
          <div className="studio-toolbox-divider" />
          <div className="studio-panel-label">画布内容</div>
          <div className="studio-toolbox-stat"><span>节点</span><strong>{nodes.length}</strong></div>
          <div className="studio-toolbox-stat"><span>连线</span><strong>{edges.length}</strong></div>
          <div className="studio-toolbox-divider" />
          <div className="studio-panel-label">成片导出</div>
          <p className="studio-toolbox-hint">按视频节点在画布中从上到下的顺序拼接。已就绪 {readyVideoCount}/{videoNodes.length} 段。</p>
          <button onClick={() => void exportVideo()} disabled={exporting || videoNodes.length === 0 || readyVideoCount !== videoNodes.length} className="studio-run-button">
            {exporting ? <Loader2 size={15} className="animate-spin" /> : <Film size={15} />}{exporting ? '正在导出…' : '导出 MP4'}
          </button>
          {exportError && <p role="alert" className="mt-2 text-xs leading-5 text-red-600">{exportError}</p>}
          {exports[0] && <div className="studio-export-preview"><video src={mediaUrl(exports[0].path)} controls preload="metadata" /><a href={mediaUrl(exports[0].path)} download={`${title || 'LeonTV-成片'}.mp4`}><Download size={14} /> 下载最新成片</a></div>}
          <p className="studio-toolbox-tip">空格拖动画布 · 滚轮缩放 · 点击节点查看详情 · Delete 删除选中节点</p>
        </aside>}

        <div className="studio-flow-wrap">
          {nodes.length === 0 && <div className="studio-empty-canvas"><div className="studio-empty-icon"><LayoutGrid size={31} /></div><h2>从一个节点开始</h2><p>在左侧添加文本、分镜、图片或视频节点，串起你的创作流程。</p><button onClick={() => addNode('text')}><Plus size={17} /> 添加文本节点</button></div>}
          <ReactFlow<StudioNode, StudioEdge>
            nodes={nodes} edges={edges} nodeTypes={nodeTypes}
            onNodesChange={changes => { if (changes.some(change => change.type === 'remove')) { captureHistory(); markDirty(); } baseOnNodesChange(changes); }}
            onEdgesChange={changes => { if (changes.some(change => change.type === 'remove')) { captureHistory(); markDirty(); } baseOnEdgesChange(changes); }}
            onNodeDragStart={() => captureHistory()}
            onNodeDragStop={() => markDirty()}
            onConnect={connection => { if (!validateConnection(connection)) { setNotice('连线类型不匹配，或会形成循环。'); return; } captureHistory(); setEdges(current => addEdge({ ...connection, type: 'smoothstep', animated: false, style: { stroke: '#9eaac4', strokeWidth: 2 } }, current)); markDirty(); }}
            isValidConnection={validateConnection}
            onSelectionChange={({ nodes: selected }) => setSelectedId(selected[0]?.id || null)}
            fitView fitViewOptions={{ padding: 0.2, maxZoom: 1 }} minZoom={0.15} maxZoom={2}
            panOnScroll zoomOnScroll={false} zoomOnPinch zoomOnDoubleClick
            deleteKeyCode={['Backspace', 'Delete']} selectionOnDrag panOnDrag={[1, 2]}>
            <Background color="#d8deea" gap={22} size={1} />
            <Controls position="bottom-left" showInteractive={false} />
            <MiniMap position="bottom-right" nodeColor={node => KIND_COLOR[(node.data as StudioNodeData).kind] || '#8c95a9'} maskColor="rgba(236,240,247,.7)" pannable zoomable />
          </ReactFlow>
          {notice && <div role="status" className="studio-notice">{notice}<button onClick={() => setNotice('')} aria-label="关闭提示"><X size={14} /></button></div>}
        </div>

        {variant === 'standalone' && <aside className="studio-inspector">
          <div className="studio-panel-label">节点属性</div>
          {!selectedNode ? <div className="studio-inspector-empty"><div><ChevronDown size={23} /></div><h3>选择一个节点</h3><p>查看内容、生成参数与历史版本。</p></div> : <>
            <div className="studio-inspector-kind" style={{ color: KIND_COLOR[selectedNode.data.kind] }}>{KIND_LABEL[selectedNode.data.kind]} {selectedNode.data.sourceStage && <span>六阶段产物</span>}</div>
            <label className="studio-field-label">节点名称</label>
            <input value={selectedNode.data.title || ''} disabled={Boolean(selectedNode.data.sourceStage)} onChange={event => updateNode(selectedNode.id, { title: event.target.value })} className="studio-field-input" />
            <label className="studio-field-label">{selectedNode.data.kind === 'image' || selectedNode.data.kind === 'video' ? '生成提示词' : '内容 / 描述'}</label>
            <textarea value={selectedNode.data.description || ''} disabled={Boolean(selectedNode.data.sourceStage)} onChange={event => updateNode(selectedNode.id, { description: event.target.value }, true)} rows={6} placeholder="在这里描述创作内容…" className="studio-field-input resize-y" />
            {selectedNode.data.sourceStage ? <div className="studio-source-note">这是原六阶段项目的产物。画布中的移动、连线与版本查看会保存；修改生成内容请进入阶段视图，避免两处数据不一致。<button onClick={() => void leaveCanvas(`/?session=${encodeURIComponent(sourceSessionId || '')}&stage=${encodeURIComponent(selectedNode.data.sourceStage || '')}`)}>打开阶段视图 <ArrowUpRight size={13} /></button></div> : (selectedNode.data.kind === 'image' || selectedNode.data.kind === 'video') && <>
              <label className="studio-field-label">模型</label>
              <select value={selectedModelId} onChange={event => updateNode(selectedNode.id, { model: event.target.value })} className="studio-field-input">
                <ModelOptions value={selectedModelId} groups={selectedModelGroups} />
              </select>
              <button onClick={() => void runNode(selectedNode)} disabled={['running', 'submitting'].includes(selectedNode.data.status || '')} className="studio-run-button">{['running', 'submitting'].includes(selectedNode.data.status || '') ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} fill="currentColor" />}{['running', 'submitting'].includes(selectedNode.data.status || '') ? '生成中…' : selectedNode.data.jobId ? '查询原任务' : selectedNode.data.mediaPath ? '生成新版本' : '运行节点'}</button>
              {typeof selectedNode.data.providerTaskId === 'string' && selectedNode.data.providerTaskId && <p className="mt-2 break-all text-xs text-[#858fa4]">任务编号：{selectedNode.data.providerTaskId}</p>}
              {selectedNode.data.error && <div role="alert" className="mt-3 text-xs leading-5 text-red-600">{selectedNode.data.error}{/密钥|配置/.test(selectedNode.data.error) && <button onClick={() => router.push('/settings')} className="mt-2 block font-semibold underline">打开模型设置</button>}</div>}
            </>}
            {selectedNode.data.mediaPath && <div className="studio-inspector-media">{selectedNode.data.kind === 'image' || selectedNode.data.kind === 'character' || selectedNode.data.kind === 'scene' ? <img src={mediaUrl(selectedNode.data.mediaPath)} alt={selectedNode.data.title} /> : <video src={mediaUrl(selectedNode.data.mediaPath)} controls preload="metadata" />}<a href={mediaUrl(selectedNode.data.mediaPath)} target="_blank" rel="noreferrer">打开素材 <ArrowUpRight size={13} /></a></div>}
            {Boolean(selectedNode.data.versions?.length) && <><label className="studio-field-label">历史版本</label><select value={selectedNode.data.mediaPath || ''} onChange={event => updateNode(selectedNode.id, { mediaPath: event.target.value }, true)} className="studio-field-input">{selectedNode.data.versions?.map((version, index) => <option key={`${version}-${index}`} value={version}>版本 {index + 1}</option>)}</select></>}
            {selectedNode.data.status === 'stale' && <div className="studio-stale-note"><CircleAlert size={15} /> 上游内容已变化，此节点需要更新。</div>}
          </>}
        </aside>}
      </div>
    </div>
  );
}
