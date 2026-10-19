/** Preserve the existing six-stage deep links while the default entry becomes Creator. */
export function isLegacyWorkflowQuery(query: Record<string, string | string[] | undefined>): boolean {
  return query.session !== undefined || query.stage !== undefined || query.legacy === 'workflow';
}
