import type { PlannerDetail, PlannerFindingResolution, PlannerPlan, PlannerRecheckReport, PlannerRepairBinding, PlannerRepairTask, PlannerReviewBinding, PlannerReviewReport, PlannerReviewTarget, PlannerRevisionCycleDetail, PlannerRevisionCycleSummary, PlannerRevisionScenario, PlannerShot, PlannerSummary } from './briefPlannerTypes';

export const PLANNER_REVISION_SCENARIOS: PlannerRevisionScenario[] = ['revision_accepted', 'revision_rejected', 'revision_needs_input', 'revision_invalid', 'revision_response_unknown', 'recheck_unknown', 'recheck_response_unknown', 'recheck_invalid'];
const FIELDS = ['schema_version','evidence_kind','roles','status','planner_call_count','reviewer_call_count','repairer_call_count','rechecker_call_count',
  'max_total_calls','max_reviewer_calls','max_repairer_calls','max_rechecker_calls','paid_call_limit','first_review_binding','first_report_hash',
  'repair_binding','recheck_binding','recheck_report_hash','accepted_plan_version'];
const DETAIL_FIELDS = ['parent_plan','first_report','repair_task','recheck_report'];
const STATUSES = ['awaiting_plan','review_ready','review_running','review_response_unknown','review_invalid','needs_input','repair_ready','repair_running',
  'repair_invalid','repair_response_unknown','recheck_ready','recheck_running','recheck_response_unknown','recheck_invalid','accepted','rejected','unknown'];
const SHOT_FIELDS = ['id','duration_ms','visual','camera','audio','depends_on'] as const;
const ROLES = ['planner','reviewer','repairer','rechecker'];
const isCount = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const isHash = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const isOne = (v: unknown): v is 0 | 1 => v === 0 || v === 1;

/** Inject the unchanged D18 readers; no circular dependency on the API client. */
export interface RevisionContracts {
  invalid(): never;
  binding(value: unknown, task: Pick<PlannerSummary, 'id' | 'source' | 'plan_version'>): PlannerReviewBinding;
  report(value: unknown, binding: PlannerReviewBinding, plan: PlannerPlan): PlannerReviewReport;
  repair(report: PlannerReviewReport, hash: string, plan: PlannerPlan): PlannerRepairTask;
  plan(value: unknown, snapshot: string): PlannerPlan;
  json(value: unknown): string;
  digest(value: unknown): Promise<string>;
}
export interface PlannerRevisionChange { shot_id: string; field: typeof SHOT_FIELDS[number]; before: PlannerShot[keyof PlannerShot]; after: PlannerShot[keyof PlannerShot] }
export function plannerRevisionChanges(parent: PlannerPlan, current: PlannerPlan): PlannerRevisionChange[] {
  return parent.shots.flatMap((before, index) => {
    const after = current.shots[index];
    if (!after || before.id !== after.id) return [];
    return SHOT_FIELDS.filter(field => JSON.stringify(before[field]) !== JSON.stringify(after[field])).map(field => ({ shot_id: before.id, field, before: before[field], after: after[field] }));
  });
}

export class PlannerRevisionCycleReader {
  constructor(private contracts: RevisionContracts) {}
  private fail(): never { return this.contracts.invalid(); }
  private object(value: unknown): Record<string, unknown> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return this.fail();
    return value as Record<string, unknown>;
  }
  private exact(value: Record<string, unknown>, fields: string[]) { if (Object.keys(value).sort().join('|') !== [...fields].sort().join('|')) this.fail(); }
  private same(a: unknown, b: unknown) { return this.contracts.json(a) === this.contracts.json(b); }
  private text(value: unknown): string {
    if (typeof value !== 'string' || !value.trim() || Array.from(value).length > 2000) return this.fail();
    this.contracts.json(value); return value;
  }
  private segments(value: unknown): string[] {
    if (!Array.isArray(value) || !value.length || value.length > 250 || !value.every(v => typeof v === 'string' && /^SRC\d{4}$/.test(v) && Number(v.slice(3)) >= 1 && Number(v.slice(3)) <= 250)) return this.fail();
    return [...value];
  }
  private targets(value: unknown, plan: PlannerPlan): PlannerReviewTarget[] {
    if (!Array.isArray(value) || !value.length || value.length > 128) return this.fail();
    return value.map(raw => {
      const t = this.object(raw); this.exact(t, ['shot_id','field']);
      if (!(t.shot_id === '*' && t.field === '$' || plan.shots.some(s => s.id === t.shot_id) && SHOT_FIELDS.includes(t.field as typeof SHOT_FIELDS[number]))) return this.fail();
      return { shot_id: t.shot_id as string, field: t.field as PlannerReviewTarget['field'] };
    });
  }
  summary(value: unknown, task: PlannerSummary): PlannerRevisionCycleSummary {
    const c = this.object(value);
    if (FIELDS.some(field => !(field in c)) || Object.keys(c).some(field => !FIELDS.includes(field) && !DETAIL_FIELDS.includes(field))) return this.fail();
    if (c.schema_version !== 1 || c.evidence_kind !== 'synthetic' || !this.same(c.roles, ROLES) || typeof c.status !== 'string' || !STATUSES.includes(c.status) ||
      !isCount(c.planner_call_count) || !isOne(c.reviewer_call_count) || !isOne(c.repairer_call_count) || !isOne(c.rechecker_call_count) ||
      c.max_total_calls !== 6 || c.max_reviewer_calls !== 1 || c.max_repairer_calls !== 1 || c.max_rechecker_calls !== 1 || c.paid_call_limit !== 0 ||
      c.planner_call_count + c.reviewer_call_count + c.repairer_call_count + c.rechecker_call_count !== task.step_count || task.step_count > 6 || task.plan_version > 2 ||
      !(c.first_report_hash === null || isHash(c.first_report_hash)) || !(c.recheck_report_hash === null || isHash(c.recheck_report_hash))) return this.fail();
    const first = c.first_review_binding === null ? null : this.contracts.binding(c.first_review_binding, { ...task, plan_version: 1 });
    const recheck = c.recheck_binding === null ? null : this.contracts.binding(c.recheck_binding, { ...task, plan_version: 2 });
    if (Boolean(first) !== Boolean(c.reviewer_call_count) || Boolean(recheck) !== Boolean(c.rechecker_call_count) ||
      c.first_report_hash !== null && !first || c.recheck_report_hash !== null && !recheck ||
      c.repairer_call_count > c.reviewer_call_count || c.rechecker_call_count > c.repairer_call_count ||
      c.repairer_call_count && c.first_report_hash === null || c.rechecker_call_count && task.plan_version !== 2) return this.fail();
    let repair: PlannerRepairBinding | null = null;
    if (c.repair_binding !== null) {
      const b = this.object(c.repair_binding);
      this.exact(b, ['task_id','run_id','intent_id','source_snapshot_hash','parent_plan_version','parent_plan_hash','review_id','report_hash','repair_task_hash','next_plan_version']);
      if (!first || b.task_id !== task.id || b.run_id !== task.id || typeof b.intent_id !== 'string' || !/^[a-f0-9]{32}$/.test(b.intent_id) ||
        b.intent_id === first.intent_id || b.source_snapshot_hash !== task.source.snapshot_hash || b.parent_plan_version !== 1 || b.parent_plan_hash !== first.plan_hash ||
        b.review_id !== first.review_id || b.report_hash !== c.first_report_hash || !isHash(b.repair_task_hash) || b.next_plan_version !== 2) return this.fail();
      repair = { task_id: task.id, run_id: task.id, intent_id: b.intent_id, source_snapshot_hash: task.source.snapshot_hash, parent_plan_version: 1,
        parent_plan_hash: first.plan_hash, review_id: first.review_id, report_hash: c.first_report_hash as string, repair_task_hash: b.repair_task_hash, next_plan_version: 2 };
    }
    if (Boolean(repair) !== Boolean(c.repairer_call_count) || recheck && (!first || !repair || recheck.review_id === first.review_id ||
      recheck.intent_id === first.intent_id || recheck.intent_id === repair.intent_id)) return this.fail();
    const phase = c.status;
    if (phase === 'awaiting_plan' && (task.plan_version !== 0 || first) || phase === 'review_ready' && (task.plan_version !== 1 || first) ||
      ['review_running','review_response_unknown','review_invalid'].includes(phase) && (!first || c.first_report_hash !== null || repair || task.plan_version !== 1) ||
      ['needs_input','repair_ready'].includes(phase) && (!first || c.first_report_hash === null || repair || task.plan_version !== 1) ||
      ['repair_running','repair_invalid','repair_response_unknown'].includes(phase) && (!repair || recheck || task.plan_version !== 1) ||
      phase === 'recheck_ready' && (!repair || recheck || task.plan_version !== 2) ||
      ['recheck_running','recheck_response_unknown','recheck_invalid'].includes(phase) && (!recheck || c.recheck_report_hash !== null) ||
      ['rejected','unknown'].includes(phase) && (!recheck || c.recheck_report_hash === null) ||
      phase === 'accepted' && (!first || c.first_report_hash === null || c.accepted_plan_version !== task.plan_version || task.plan_version !== 1 && task.plan_version !== 2 || task.plan_version === 2 && (!recheck || c.recheck_report_hash === null)) ||
      phase !== 'accepted' && c.accepted_plan_version !== null) return this.fail();
    const active = phase.startsWith('review_') ? first : phase.startsWith('repair_') ? repair : phase.startsWith('recheck_') ? recheck : null;
    if (task.pending_call && active && task.pending_call.operation_id !== active.intent_id || phase.endsWith('_response_unknown') && task.pending_call?.status !== 'intent') return this.fail();
    return { schema_version: 1, evidence_kind: 'synthetic', roles: ['planner','reviewer','repairer','rechecker'], status: phase as PlannerRevisionCycleSummary['status'],
      planner_call_count: c.planner_call_count, reviewer_call_count: c.reviewer_call_count, repairer_call_count: c.repairer_call_count, rechecker_call_count: c.rechecker_call_count,
      max_total_calls: 6, max_reviewer_calls: 1, max_repairer_calls: 1, max_rechecker_calls: 1, paid_call_limit: 0,
      first_review_binding: first, first_report_hash: c.first_report_hash as string | null, repair_binding: repair, recheck_binding: recheck,
      recheck_report_hash: c.recheck_report_hash as string | null, accepted_plan_version: c.accepted_plan_version as 1 | 2 | null };
  }
  private recheck(value: unknown, cycle: PlannerRevisionCycleSummary, first: PlannerReviewReport, plan: PlannerPlan): PlannerRecheckReport {
    const r = this.object(value); this.exact(r, ['binding','parent_review','resolutions','rules','overall_verdict']);
    const parent = first.binding, binding = cycle.recheck_binding!;
    const parentReview = { review_id: parent.review_id, plan_version: 1 as const, plan_hash: parent.plan_hash, report_hash: cycle.first_report_hash! };
    if (!this.same(r.parent_review, parentReview) || !Array.isArray(r.rules)) return this.fail();
    const prior = new Map(first.rules.filter(rule => rule.verdict !== 'accepted').flatMap(rule => rule.findings).map(f => [f.finding_id, f]));
    const priorRefs = new Map<string, string | null>();
    const rawRules = r.rules.map(raw => {
      const rule = this.object(raw);
      if (!Array.isArray(rule.findings)) return this.fail();
      return { ...rule, findings: rule.findings.map(raw => {
        const finding = this.object(raw), ref = finding.prior_finding_id;
        if (!('prior_finding_id' in finding) || !(ref === null || typeof ref === 'string' && prior.get(ref)?.rule_id === rule.rule_id)) return this.fail();
        if (typeof finding.finding_id !== 'string') return this.fail();
        priorRefs.set(finding.finding_id, ref as string | null);
        return { ...finding, prior_finding_id: null };
      }) };
    });
    // Reuse strict D18 shape/locations/aggregation without relaxing its first-review contract.
    const checked = this.contracts.report({ binding: r.binding, rules: rawRules, overall_verdict: r.overall_verdict }, binding, plan);
    const rules = checked.rules.map(rule => ({ ...rule, findings: rule.findings.map(f => ({ ...f, prior_finding_id: priorRefs.get(f.finding_id)! })) }));
    if (!Array.isArray(r.resolutions) || r.resolutions.length !== prior.size) return this.fail();
    const seen = new Set<string>();
    const resolutions: PlannerFindingResolution[] = r.resolutions.map(raw => {
      const resolution = this.object(raw); this.exact(resolution, ['prior_finding_id','rule_id','status','source_segment_ids','targets','note']);
      const id = resolution.prior_finding_id;
      if (typeof id !== 'string' || !prior.has(id) || seen.has(id) || resolution.rule_id !== prior.get(id)!.rule_id ||
        !['resolved','unresolved','unknown'].includes(String(resolution.status)) || typeof resolution.status !== 'string') return this.fail();
      seen.add(id);
      const rule = rules.find(rule => rule.rule_id === resolution.rule_id)!;
      const linked = rule.findings.filter(f => f.prior_finding_id === id);
      if (resolution.status === 'resolved' ? linked.length !== 0 : !linked.length || rule.verdict !== (resolution.status === 'unresolved' ? 'rejected' : 'unknown')) return this.fail();
      const sourceIds = this.segments(resolution.source_segment_ids), located = this.targets(resolution.targets, plan), original = prior.get(id)!;
      if (original.source_segment_ids.some(source => !sourceIds.includes(source)) || original.targets.some(target => !located.some(t => t.shot_id === target.shot_id && t.field === target.field))) return this.fail();
      return { prior_finding_id: id, rule_id: prior.get(id)!.rule_id, status: resolution.status as PlannerFindingResolution['status'],
        source_segment_ids: sourceIds, targets: located, note: this.text(resolution.note) };
    });
    if (checked.overall_verdict === 'accepted' && resolutions.some(r => r.status !== 'resolved')) return this.fail();
    const report = { binding, parent_review: parentReview, resolutions, rules, overall_verdict: checked.overall_verdict };
    if (new TextEncoder().encode(JSON.stringify(report)).byteLength > 65536) return this.fail();
    return report;
  }
  detail(value: unknown, cycle: PlannerRevisionCycleSummary, task: PlannerDetail): PlannerRevisionCycleDetail {
    const c = this.object(value); this.exact(c, [...FIELDS, ...DETAIL_FIELDS]);
    if (task.plan_versions.length !== task.plan_version || task.plan_versions.some((v,i) => v.version !== i + 1)) return this.fail();
    const parent = c.parent_plan === null ? null : this.contracts.plan(c.parent_plan, task.source.snapshot_hash);
    if (task.plan_version === 0 ? parent !== null || task.plan !== null : !parent || !task.plan) return this.fail();
    if (parent && task.plan_version === 1 && !this.same(parent, task.plan)) return this.fail();
    if (cycle.first_review_binding && (!parent || task.plan_versions[0]?.plan_hash !== cycle.first_review_binding.plan_hash)) return this.fail();
    if (cycle.recheck_binding && task.plan_versions[1]?.plan_hash !== cycle.recheck_binding.plan_hash) return this.fail();
    let first: PlannerReviewReport | null = null, repair: PlannerRepairTask | null = null, recheck: PlannerRecheckReport | null = null;
    if (c.first_report !== null) {
      if (!parent || !cycle.first_review_binding || !cycle.first_report_hash) return this.fail();
      first = this.contracts.report(c.first_report, cycle.first_review_binding, parent);
      if (first.overall_verdict !== 'accepted') {
        repair = this.contracts.repair(first, cycle.first_report_hash, parent);
        if (!this.same(c.repair_task, repair)) return this.fail();
      } else if (c.repair_task !== null || cycle.status !== 'accepted' || task.plan_version !== 1 || cycle.repairer_call_count || cycle.rechecker_call_count) return this.fail();
      if ((first.overall_verdict === 'unknown' || repair?.kind === 'needs_input') && (cycle.status !== 'needs_input' || cycle.repairer_call_count || task.plan_version !== 1)) return this.fail();
    } else if (cycle.first_report_hash !== null || c.repair_task !== null || cycle.repairer_call_count || task.plan_version === 2) return this.fail();
    if (cycle.repair_binding && (!repair || repair.kind !== 'content_repair_ready' || first?.overall_verdict !== 'rejected')) return this.fail();
    if (task.plan_version === 2) {
      if (!parent || !task.plan || !repair || repair.kind !== 'content_repair_ready' || !cycle.repair_binding ||
        !this.same(parent.shots.map(s => s.id), task.plan.shots.map(s => s.id))) return this.fail();
      const changed = plannerRevisionChanges(parent, task.plan), allowed = new Set(repair.repair_targets.map(t => `${t.shot_id}|${t.field}`));
      if (!changed.length || changed.some(t => !allowed.has(`${t.shot_id}|${t.field}`))) return this.fail();
    }
    if (c.recheck_report !== null) {
      if (!first || !task.plan || !cycle.recheck_binding || !cycle.recheck_report_hash || task.plan_version !== 2) return this.fail();
      recheck = this.recheck(c.recheck_report, cycle, first, task.plan);
      if (cycle.status !== recheck.overall_verdict) return this.fail();
    } else if (cycle.recheck_report_hash !== null || ['rejected','unknown'].includes(cycle.status) || cycle.status === 'accepted' && task.plan_version === 2) return this.fail();
    if (cycle.status === 'accepted' && task.plan_version === 1 && first?.overall_verdict !== 'accepted') return this.fail();
    return { ...cycle, parent_plan: parent, first_report: first, repair_task: repair, recheck_report: recheck };
  }
  async verify(task: PlannerDetail): Promise<void> {
    const c = task.revision_cycle;
    if (!c) return;
    if (c.parent_plan && await this.contracts.digest(c.parent_plan) !== task.plan_versions[0]?.plan_hash) return this.fail();
    if (task.plan && await this.contracts.digest(task.plan) !== task.plan_versions.at(-1)?.plan_hash) return this.fail();
    if (c.first_report && await this.contracts.digest(c.first_report) !== c.first_report_hash) return this.fail();
    if (c.repair_binding && (!c.repair_task || await this.contracts.digest(c.repair_task) !== c.repair_binding.repair_task_hash)) return this.fail();
    if (c.recheck_report && await this.contracts.digest(c.recheck_report) !== c.recheck_report_hash) return this.fail();
  }
}
