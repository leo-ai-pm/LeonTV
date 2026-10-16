/** Normalize persisted asset paths without changing external URLs or local previews. */
export function mediaUrl(path?: string): string {
  if (!path) return '';
  if (/^(https?:\/\/|blob:|data:)/i.test(path)) return path;
  const clean = path.replace(/\\/g, '/');
  const mounted = clean.indexOf('/code/');
  if (mounted >= 0) return clean.slice(mounted);
  const normalized = clean.replace(/^\/+/, '');
  if (normalized.startsWith('code/')) return `/${normalized}`;
  if (normalized.startsWith('result/')) return `/code/${normalized}`;
  // Absolute application paths (for example /ui/...) are already browser URLs.
  if (path.startsWith('/')) return path;
  return `/code/result/${normalized}`;
}
