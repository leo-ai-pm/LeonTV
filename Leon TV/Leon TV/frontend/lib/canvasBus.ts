/**
 * 画布命令总线：把画布内部的操作暴露给画布之外的面板（当前是右侧 Agent 面板）。
 *
 * 纯逻辑、无 React 依赖，便于单元测试。它只做命令转发、快照读取与选中态广播，
 * 不持有画布数据，也不发起任何生成请求。
 */
import type { StudioKind, StudioNode, StudioNodeData } from '@/lib/studioApi';

/** 供 Agent 阅读的画布摘要（不含完整节点对象）。 */
export interface CanvasNodeSummary {
  id: string;
  kind: string;
  title: string;
  description?: string;
  status?: string;
  mediaPath?: string;
  duration?: number;
}

export interface CanvasSnapshot {
  title: string;
  nodes: CanvasNodeSummary[];
}

export interface CanvasHandlers {
  addNode: (kind: StudioKind, patch?: Partial<StudioNodeData>) => void;
  updateNode: (id: string, patch: Partial<StudioNodeData>) => void;
  deleteNode: (id: string) => void;
  runNode: (id: string) => void;
  exportVideo: () => void;
  runPipeline: (scope: 'single' | 'all') => void;
  snapshot: () => CanvasSnapshot;
}

export type CanvasCommand =
  | { type: 'add-node'; kind: StudioKind; patch?: Partial<StudioNodeData> }
  | { type: 'update-node'; id: string; patch: Partial<StudioNodeData> }
  | { type: 'delete-node'; id: string }
  | { type: 'run-node'; id: string }
  | { type: 'export-video' }
  | { type: 'run-pipeline'; scope: 'single' | 'all' };

export interface CanvasBus {
  register(handlers: CanvasHandlers): () => void;
  dispatch(command: CanvasCommand): boolean;
  snapshot(): CanvasSnapshot | null;
  setSelection(node: StudioNode | null): void;
  getSelection(): StudioNode | null;
  subscribe(listener: () => void): () => void;
}

export function createCanvasBus(): CanvasBus {
  let handlers: CanvasHandlers | null = null;
  let selection: StudioNode | null = null;
  const listeners = new Set<() => void>();

  const notify = () => { for (const listener of listeners) listener(); };

  return {
    register(next) {
      handlers = next;
      return () => { if (handlers === next) handlers = null; };
    },
    dispatch(command) {
      if (!handlers) return false;
      switch (command.type) {
        case 'add-node': handlers.addNode(command.kind, command.patch); return true;
        case 'update-node': handlers.updateNode(command.id, command.patch); return true;
        case 'delete-node': handlers.deleteNode(command.id); return true;
        case 'run-node': handlers.runNode(command.id); return true;
        case 'export-video': handlers.exportVideo(); return true;
        case 'run-pipeline': handlers.runPipeline(command.scope); return true;
        default: return false;
      }
    },
    snapshot: () => handlers?.snapshot() ?? null,
    setSelection(node) {
      if (selection === node) return;
      selection = node;
      notify();
    },
    getSelection: () => selection,
    subscribe(listener) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
}
