'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import {
  ReactFlow, Background, Controls, Handle, MiniMap, Position, addEdge, useEdgesState, useNodesState,
  type Connection, type NodeProps,
} from '@xyflow/react';
import { ArrowLeft, ArrowUpRight, Check, ChevronDown, CircleAlert, Clapperboard, FileText, Film, Image as ImageIcon, LayoutGrid, Loader2, Play, Plus, Redo2, Save, Sparkles, Type, Undo2, Video, X } from 'lucide-react';
import { fetchModelGroupsByType } from '@/lib/modelRegistry';
import { getStudioProject, mediaUrl, saveStudioProject, type StudioEdge, type StudioKind, type StudioNode, type StudioNodeData } from '@/lib/studioApi';
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

export default function StudioCanvas() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
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
  const [modelGroups, setModelGroups] = useState<Record<'t2i' | 'video', ProviderGroup[]>>({ t2i: [], video: [] });
  const [defaultModels, setDefaultModels] = useState({ image: '', video: '' });
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
    Promise.all([fetchModelGroupsByType('t2i'), fetchModelGroupsByType('video')])
      .then(([t2i, video]) => setModelGroups({ t2i, video })).catch(() => {});
    fetch('/api/config').then(response => response.json()).then(payload => {
      const models = payload.config?.models || {};
      setDefaultModels({ image: models.image_t2i || '', video: models.video_first_frame || models.video || '' });
    }).catch(() => {});
  }, []);

  const queueSave = useCallback(() => {
    saveChainRef.current = saveChainRef.current.catch(() => {}).then(async () => {
      const seq = editSeqRef.current;
      if (seq === savedSeqRef.current) return;
      setSaveState('saving');
      const current = latestRef.current;
      try {
        const saved = await saveStudioProject(projectId, revisionRef.current, current.title, current.nodes, current.edges);
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

  const selectedNode = useMemo(() => nodes.find(node => node.id === selectedId) || null, [nodes, selectedId]);

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

  const updateNode = (nodeId: string, patch: Partial<StudioNodeData>, affectsDownstream = false) => {
    const stale = affectsDownstream ? descendants(nodeId, edges) : new Set<string>();
    setNodes(current => current.map(node => node.id === nodeId
      ? { ...node, data: { ...node.data, ...patch } }
      : stale.has(node.id) && node.data.status !== 'running'
        ? { ...node, data: { ...node.data, status: 'stale' } }
        : node));
    markDirty();
  };

  const addNode = (kind: StudioKind) => {
    captureHistory();
    const model = kind === 'image' ? defaultModels.image : kind === 'video' ? defaultModels.video : '';
    const node = newNode(kind, nodes.length, model);
    setNodes(current => [...current.map(item => ({ ...item, selected: false })), { ...node, selected: true }]);
    setSelectedId(node.id);
    markDirty();
  };

  const validateConnection = useCallback((connection: Connection | StudioEdge) => canConnect(connection, nodes, edges), [nodes, edges]);

  const runNode = async (node: StudioNode) => {
    const kind = node.data.kind;
    if (node.data.sourceStage) { setNotice('六阶段产物请在原流程页继续生成；画布目前用于查看与编排。'); return; }
    if (kind !== 'image' && kind !== 'video') return;
    const prompt = (node.data.description || '').trim();
    if (!prompt) { setNotice('请先在右侧填写生成提示词。'); return; }
    const model = node.data.model || (kind === 'image' ? defaultModels.image : defaultModels.video);
    if (!model) { setNotice('当前没有可用模型，请先前往设置配置模型。'); return; }
    const parent = edges.map(edge => edge.target === node.id ? nodes.find(item => item.id === edge.source) : null)
      .find(item => item?.data.mediaPath && item.data.kind === 'image');
    const image = parent?.data.mediaPath || '';
    setNotice('');
    try {
      const provider = modelGroups[kind === 'image' ? 't2i' : 'video']
        .find(group => group.models.some(option => option.id === model))?.provider;
      if (provider && provider !== 'unknown') {
        const configResponse = await fetch('/api/config');
        if (!configResponse.ok) throw new Error('无法读取模型配置，请稍后重试。');
        const config = (await configResponse.json()).config?.api_providers?.[provider];
        const configured = provider === 'kling'
          ? Boolean(config?.access_key && config?.secret_key)
          : Boolean(config?.api_key);
        if (!configured) throw new Error(`尚未配置 ${provider} 的模型密钥。请前往设置填写后重试。`);
      }
      updateNode(node.id, { status: 'running', error: '' });
      const endpoint = kind === 'video' ? 'video' : image ? 'i2i' : 't2i';
      const response = await fetch(`/api/sandbox/${endpoint}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, model, ...(image ? { image } : {}), ...(kind === 'image' ? { ratio: '16:9', style: 'realistic' } : {}) }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.success) throw new Error(payload.error || payload.detail || '生成失败');
      const path = kind === 'image' ? payload.result?.[0] : payload.video_path;
      if (!path) throw new Error('模型没有返回素材。请检查模型与密钥配置，或更换模型重试。');
      const current = latestRef.current.nodes.find(item => item.id === node.id);
      const versions = Array.from(new Set([...(current?.data.versions || []), path]));
      updateNode(node.id, { mediaPath: path, versions, status: 'completed', error: '' }, true);
      setNotice('已生成新版本，并保留旧版本。');
    } catch (cause) {
      updateNode(node.id, { status: 'error', error: cause instanceof Error ? cause.message : '生成失败' });
    }
  };

  if (loading) return <div className="studio-canvas-loading"><Loader2 size={24} className="animate-spin" /> 正在打开项目画布…</div>;
  if (loadError) return <div className="studio-canvas-loading"><CircleAlert size={23} /> {loadError}<button onClick={() => router.push('/studio')}>返回项目</button></div>;

  return (
    <div className="studio-editor">
      <header className="studio-editor-header">
        <button aria-label="返回项目" onClick={() => router.push('/studio')} className="studio-icon-button"><ArrowLeft size={18} /></button>
        <div className="studio-header-divider" />
        <div className="flex min-w-0 flex-col"><input aria-label="项目名称" value={title} onChange={event => { setTitle(event.target.value); markDirty(); }} maxLength={120} className="studio-title-input" /><span className="text-[11px] text-[#939caf]">{sourceSessionId ? '六阶段项目 · 可视画布' : '自由画布 · 桌面工作台'}</span></div>
        <div className="ml-auto flex items-center gap-3 text-xs text-[#858fa4]">
          <button onClick={() => travelHistory('undo')} disabled={!historyCounts.past} title="撤销画布结构操作（⌘/Ctrl+Z）" aria-label="撤销" className="studio-icon-button disabled:opacity-35"><Undo2 size={17} /></button>
          <button onClick={() => travelHistory('redo')} disabled={!historyCounts.future} title="重做画布结构操作（⌘/Ctrl+Shift+Z）" aria-label="重做" className="studio-icon-button disabled:opacity-35"><Redo2 size={17} /></button>
          {saveState === 'saving' ? <><Loader2 size={14} className="animate-spin" /> 保存中</> : saveState === 'saved' ? <><Check size={15} className="text-[#3a9c88]" /> 已保存</> : saveState === 'error' ? <span className="text-red-600">{saveError}</span> : '待保存'}
          <button onClick={queueSave} className="studio-save-button"><Save size={15} /> 保存</button>
          {sourceSessionId && <button onClick={() => router.push(`/?session=${encodeURIComponent(sourceSessionId)}`)} className="studio-outline-button">阶段视图 <ArrowUpRight size={15} /></button>}
        </div>
      </header>

      <div className="studio-editor-main">
        <aside className="studio-toolbox">
          <div className="studio-panel-label">创作节点</div>
          <p className="studio-toolbox-hint">点击添加到画布，拖动节点右侧连线至下一步。</p>
          {(['text', 'storyboard', 'image', 'video'] as StudioKind[]).map(kind => {
            const Icon = KIND_ICON[kind];
            return <button key={kind} onClick={() => addNode(kind)} className="studio-tool-button"><span style={{ color: KIND_COLOR[kind], backgroundColor: `${KIND_COLOR[kind]}1a` }}><Icon size={18} /></span><span>{KIND_LABEL[kind]}</span><Plus size={14} className="ml-auto text-[#aab3c3]" /></button>;
          })}
          <div className="studio-toolbox-divider" />
          <div className="studio-panel-label">画布内容</div>
          <div className="studio-toolbox-stat"><span>节点</span><strong>{nodes.length}</strong></div>
          <div className="studio-toolbox-stat"><span>连线</span><strong>{edges.length}</strong></div>
          <p className="studio-toolbox-tip">空格拖动画布 · 滚轮缩放 · 点击节点查看详情 · Delete 删除选中节点</p>
        </aside>

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

        <aside className="studio-inspector">
          <div className="studio-panel-label">节点属性</div>
          {!selectedNode ? <div className="studio-inspector-empty"><div><ChevronDown size={23} /></div><h3>选择一个节点</h3><p>查看内容、生成参数与历史版本。</p></div> : <>
            <div className="studio-inspector-kind" style={{ color: KIND_COLOR[selectedNode.data.kind] }}>{KIND_LABEL[selectedNode.data.kind]} {selectedNode.data.sourceStage && <span>六阶段产物</span>}</div>
            <label className="studio-field-label">节点名称</label>
            <input value={selectedNode.data.title || ''} disabled={Boolean(selectedNode.data.sourceStage)} onChange={event => updateNode(selectedNode.id, { title: event.target.value })} className="studio-field-input" />
            <label className="studio-field-label">{selectedNode.data.kind === 'image' || selectedNode.data.kind === 'video' ? '生成提示词' : '内容 / 描述'}</label>
            <textarea value={selectedNode.data.description || ''} disabled={Boolean(selectedNode.data.sourceStage)} onChange={event => updateNode(selectedNode.id, { description: event.target.value }, true)} rows={6} placeholder="在这里描述创作内容…" className="studio-field-input resize-y" />
            {selectedNode.data.sourceStage ? <div className="studio-source-note">这是原六阶段项目的产物。画布中的移动、连线与版本查看会保存；修改生成内容请进入阶段视图，避免两处数据不一致。<button onClick={() => router.push(`/?session=${encodeURIComponent(sourceSessionId || '')}&stage=${encodeURIComponent(selectedNode.data.sourceStage || '')}`)}>打开阶段视图 <ArrowUpRight size={13} /></button></div> : (selectedNode.data.kind === 'image' || selectedNode.data.kind === 'video') && <>
              <label className="studio-field-label">模型</label>
              <select value={selectedNode.data.model || ''} onChange={event => updateNode(selectedNode.id, { model: event.target.value })} className="studio-field-input">
                {!selectedNode.data.model && <option value="">选择模型</option>}
                {modelGroups[selectedNode.data.kind === 'image' ? 't2i' : 'video'].map(group => <optgroup key={group.provider} label={group.label}>{group.models.map(model => <option key={model.id} value={model.id}>{model.label}</option>)}</optgroup>)}
              </select>
              <button onClick={() => void runNode(selectedNode)} disabled={selectedNode.data.status === 'running'} className="studio-run-button">{selectedNode.data.status === 'running' ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} fill="currentColor" />}{selectedNode.data.status === 'running' ? '生成中…' : selectedNode.data.mediaPath ? '生成新版本' : '运行节点'}</button>
              {selectedNode.data.error && <div role="alert" className="mt-3 text-xs leading-5 text-red-600">{selectedNode.data.error}{/密钥|配置/.test(selectedNode.data.error) && <button onClick={() => router.push('/settings')} className="mt-2 block font-semibold underline">打开模型设置</button>}</div>}
            </>}
            {selectedNode.data.mediaPath && <div className="studio-inspector-media">{selectedNode.data.kind === 'image' || selectedNode.data.kind === 'character' || selectedNode.data.kind === 'scene' ? <img src={mediaUrl(selectedNode.data.mediaPath)} alt={selectedNode.data.title} /> : <video src={mediaUrl(selectedNode.data.mediaPath)} controls preload="metadata" />}<a href={mediaUrl(selectedNode.data.mediaPath)} target="_blank" rel="noreferrer">打开素材 <ArrowUpRight size={13} /></a></div>}
            {Boolean(selectedNode.data.versions?.length) && <><label className="studio-field-label">历史版本</label><select value={selectedNode.data.mediaPath || ''} onChange={event => updateNode(selectedNode.id, { mediaPath: event.target.value }, true)} className="studio-field-input">{selectedNode.data.versions?.map((version, index) => <option key={`${version}-${index}`} value={version}>版本 {index + 1}</option>)}</select></>}
            {selectedNode.data.status === 'stale' && <div className="studio-stale-note"><CircleAlert size={15} /> 上游内容已变化，此节点需要更新。</div>}
          </>}
        </aside>
      </div>
    </div>
  );
}
