'use client';

import { useMemo, useRef, useState, useSyncExternalStore } from 'react';
import {
  Bot, Film, Image as ImageIcon, Loader2, Play, Send, Sparkles, Trash2, Type, Video, Clapperboard, Wand2,
} from 'lucide-react';
import type { CanvasBus } from '@/lib/canvasBus';
import type { StudioKind } from '@/lib/studioApi';
import { AGENT_MODEL, applyAgentActions, buildAgentPrompt, parseAgentReply } from '@/lib/agentChat';

const NODE_KINDS: Array<{ kind: StudioKind; label: string; icon: typeof Type }> = [
  { kind: 'text', label: '文本', icon: Type },
  { kind: 'storyboard', label: '分镜', icon: Clapperboard },
  { kind: 'image', label: '参考图', icon: ImageIcon },
  { kind: 'video', label: '视频片段', icon: Video },
];

const KIND_LABEL: Record<string, string> = {
  text: '文本', script: '剧本', character: '角色', scene: '场景',
  storyboard: '分镜', image: '参考图', video: '视频片段', output: '成片',
};

interface ChatMessage {
  role: 'user' | 'agent';
  text: string;
  actions?: string[];
}

/**
 * 右侧 Agent 面板。
 * 对话走 `/api/sandbox/llm`（默认 DeepSeek Flash），模型只返回结构化动作，
 * 由前端逐条校验后通过命令总线执行；画布操作区块始终可用。
 */
export default function AgentPanel({ projectTitle, bus }: { projectTitle?: string; bus?: CanvasBus }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [chatError, setChatError] = useState('');
  const listRef = useRef<HTMLDivElement>(null);

  const selection = useSyncExternalStore(
    bus ? bus.subscribe : () => () => {},
    bus ? bus.getSelection : () => null,
    () => null,
  );

  const hint = useMemo(
    () => (projectTitle ? `当前项目：${projectTitle}` : '尚未选择项目'),
    [projectTitle],
  );

  const node = selection;
  const canGenerate = Boolean(node && (node.data.kind === 'image' || node.data.kind === 'video'));

  const send = async () => {
    const question = draft.trim();
    if (!question || !bus || sending) return;
    const snapshot = bus.snapshot();
    if (!snapshot) { setChatError('画布尚未就绪，请稍后再试。'); return; }
    setDraft('');
    setChatError('');
    setSending(true);
    setMessages(previous => [...previous, { role: 'user', text: question }]);
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 90_000);
    try {
      const response = await fetch('/api/sandbox/llm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({ model: AGENT_MODEL, prompt: buildAgentPrompt(snapshot, question) }),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok || !payload?.success) {
        throw new Error(payload?.error || `对话请求失败（HTTP ${response.status}）。`);
      }
      const allowed = new Set(snapshot.nodes.map(item => item.id));
      const names = new Map(snapshot.nodes.map(item => [item.id, item.title]));
      const turn = parseAgentReply(String(payload.result ?? ''), allowed);
      const applied = applyAgentActions(bus, turn.actions, names);
      setMessages(previous => [...previous, { role: 'agent', text: turn.reply, actions: applied }]);
    } catch (cause) {
      const aborted = cause instanceof DOMException && cause.name === 'AbortError';
      setChatError(aborted ? '对话超时，请稍后重试。' : cause instanceof Error ? cause.message : '对话失败，请稍后再试。');
    } finally {
      window.clearTimeout(timer);
      setSending(false);
      requestAnimationFrame(() => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight }); });
    }
  };

  return (
    <section className="ws-agent" aria-label="Agent 对话">
      <header className="ws-agent-head">
        <span className="ws-agent-avatar"><Bot size={16} /></span>
        <div className="min-w-0">
          <strong>制作 Agent</strong>
          <p className="ws-agent-sub">{hint}</p>
        </div>
        <span className="ws-agent-badge">{AGENT_MODEL}</span>
      </header>

      <div className="ws-agent-body" ref={listRef}>
        {messages.length === 0 && (
          <div className="ws-agent-empty">
            <span className="ws-agent-empty-icon"><Sparkles size={20} /></span>
            <p><strong>说一句，让它动手。</strong></p>
            <p className="ws-agent-muted">
              例如「加一个分镜描述海边的空镜」「把选中的参考图提示词改成夜景」。
              它只会返回结构化动作，由前端校验后再改画布；不会凭空生成素材。
            </p>
          </div>
        )}

        {messages.map((message, index) => (
          <div key={index} className="ws-msg" data-role={message.role}>
            <div className="ws-msg-bubble">{message.text}</div>
            {message.actions && message.actions.length > 0 && (
              <ul className="ws-msg-actions">
                {message.actions.map((action, actionIndex) => <li key={actionIndex}>✓ {action}</li>)}
              </ul>
            )}
          </div>
        ))}

        {sending && <div className="ws-msg" data-role="agent"><div className="ws-msg-bubble ws-msg-pending"><Loader2 size={13} className="animate-spin" /> 正在思考…</div></div>}
        {chatError && (
          <p className="ws-agent-error" role="alert">
            {chatError}
            {/api[_ ]?key|密钥|not configured/i.test(chatError) && !chatError.includes('设置页') && (
              <> <a href="/settings">前往设置</a></>
            )}
          </p>
        )}

        <div className="ws-section ws-section-tools">
          <div className="ws-section-head"><span>画布操作</span><small>不经过模型</small></div>
          <div className="ws-node-grid">
            {NODE_KINDS.map(item => {
              const Icon = item.icon;
              return (
                <button key={item.kind} type="button" className="ws-node-add" disabled={!bus}
                  onClick={() => bus?.dispatch({ type: 'add-node', kind: item.kind })}>
                  <Icon size={15} /> {item.label}
                </button>
              );
            })}
          </div>
          <button type="button" className="ws-node-export" disabled={!bus}
            onClick={() => bus?.dispatch({ type: 'export-video' })}>
            <Film size={15} /> 按画布顺序导出 MP4
          </button>
          <div className="ws-pipeline-row">
            <button type="button" className="ws-pipeline" disabled={!bus}
              onClick={() => {
                if (!window.confirm('生成第 1 镜的参考图与视频（会调用已配置的模型并产生费用）。继续？')) return;
                bus?.dispatch({ type: 'run-pipeline', scope: 'single' });
              }}>
              <Wand2 size={14} /> 生成素材（单镜）
            </button>
            <button type="button" className="ws-pipeline ws-pipeline-all" disabled={!bus}
              onClick={() => {
                if (!window.confirm('按画布顺序生成全部待生成素材，然后导出成片。每个待生成节点都会调用模型并产生费用。继续？')) return;
                bus?.dispatch({ type: 'run-pipeline', scope: 'all' });
              }}>
              <Sparkles size={14} /> 一键出片（全部）
            </button>
          </div>
        </div>

        <div className="ws-section">
          <div className="ws-section-head"><span>选中节点</span><small>{node ? KIND_LABEL[node.data.kind] || node.data.kind : '未选择'}</small></div>
          {!node && <p className="ws-agent-muted">在中间画布点选一个节点，这里就能改它。</p>}
          {node && (
            <div className="ws-node-card">
              <label className="ws-field">
                <span>名称</span>
                <input value={node.data.title || ''}
                  onChange={event => bus?.dispatch({ type: 'update-node', id: node.id, patch: { title: event.target.value } })} />
              </label>
              {canGenerate && (
                <label className="ws-field">
                  <span>生成提示词</span>
                  <textarea rows={3} value={node.data.description || ''}
                    onChange={event => bus?.dispatch({ type: 'update-node', id: node.id, patch: { description: event.target.value } })} />
                </label>
              )}
              <div className="ws-node-actions">
                {canGenerate && (
                  <button type="button" className="ws-node-run" onClick={() => bus?.dispatch({ type: 'run-node', id: node.id })}>
                    <Play size={13} /> 生成
                  </button>
                )}
                <button type="button" className="ws-node-delete" onClick={() => bus?.dispatch({ type: 'delete-node', id: node.id })}>
                  <Trash2 size={13} /> 删除
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      <form className="ws-agent-composer" onSubmit={event => { event.preventDefault(); void send(); }}>
        <textarea
          value={draft}
          onChange={event => setDraft(event.target.value)}
          onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send(); } }}
          placeholder="例如：加一个分镜，描述清晨的海边空镜"
          rows={3}
        />
        <div className="ws-agent-toolbar">
          <span className="ws-agent-note-inline">Enter 发送 · Shift+Enter 换行</span>
          <button type="submit" className="ws-agent-send" disabled={!draft.trim() || sending || !bus}>
            {sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />} 发送
          </button>
        </div>
      </form>
    </section>
  );
}
