import { plannerMediaApi, PlannerMediaError } from './plannerMediaApi';
import type { PlannerMediaBinding, PlannerMediaObservation } from './plannerMediaTypes';

export function sameMediaBinding(a: PlannerMediaBinding | null, b: PlannerMediaBinding | null): boolean {
  return Boolean(a && b && a.task_id === b.task_id && a.source_snapshot_hash === b.source_snapshot_hash);
}
export interface PlannerMediaState {
  binding: PlannerMediaBinding | null; data: PlannerMediaObservation | null;
  phase: 'idle' | 'loading' | 'refreshing' | 'ready' | 'not_found' | 'error'; error: string; observedAt: string | null;
}
export class PlannerMediaController {
  private state: PlannerMediaState = { binding: null, data: null, phase: 'idle', error: '', observedAt: null };
  private sequence = 0;
  private pending: AbortController | null = null;
  private listeners = new Set<() => void>();
  getSnapshot = () => this.state;
  getServerSnapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private set(patch: Partial<PlannerMediaState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener()); }
  invalidate() { this.sequence++; this.pending?.abort(); this.pending = null; }
  async select(binding: PlannerMediaBinding) {
    const same = sameMediaBinding(this.state.binding, binding);
    if (same && (this.pending || this.state.phase === 'ready' || this.state.phase === 'not_found')) return;
    this.invalidate();
    this.set({ binding: { ...binding }, data: null, observedAt: null, phase: 'loading', error: '' });
    await this.read();
  }
  async refresh() {
    if (!this.state.binding || this.pending) return;
    this.set({ phase: this.state.data ? 'refreshing' : 'loading', error: '' });
    await this.read();
  }
  private async read() {
    const binding = this.state.binding;
    if (!binding) return;
    const sequence = ++this.sequence, pending = new AbortController();
    this.pending = pending;
    try {
      const data = await plannerMediaApi.get(binding, pending.signal);
      // JSON may finish after selection/unmount, including when fetch ignores abort.
      if (sequence !== this.sequence || !sameMediaBinding(this.state.binding, binding)) return;
      this.set({ data, phase: data ? 'ready' : 'not_found', error: '', observedAt: new Date().toISOString() });
    } catch (error) {
      if (sequence !== this.sequence || !sameMediaBinding(this.state.binding, binding)) return;
      this.set({ phase: 'error', error: error instanceof PlannerMediaError ? error.message : '分镜图片读取失败，当前结果未核验。' });
    } finally { if (sequence === this.sequence) this.pending = null; }
  }
}
