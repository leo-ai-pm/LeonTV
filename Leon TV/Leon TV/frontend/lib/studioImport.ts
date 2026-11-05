/**
 * 画布素材导入：把已存在的图片/视频接入画布节点。
 *
 * 只做纯数据转换（扩展名 → 节点类型、上传路径 → 画布存储路径），
 * 不发起网络请求，也不触发任何生成或付费动作。
 */

export type StudioImportKind = 'image' | 'video';

export interface StudioImportResult {
  kind: StudioImportKind;
  title: string;
  mediaPath: string;
}

const IMAGE_EXTENSIONS = new Set(['.jpg', '.jpeg', '.png', '.webp', '.bmp']);
const VIDEO_EXTENSIONS = new Set(['.mp4', '.mov', '.avi', '.mkv', '.webm']);
const UPLOAD_PREFIX = /^[0-9a-f]{32}_/i;

function extensionOf(name: string): string {
  const clean = String(name || '').replace(/\\/g, '/').split('/').pop() || '';
  const dot = clean.lastIndexOf('.');
  return dot <= 0 ? '' : clean.slice(dot).toLowerCase();
}

export function importKindForName(name: string): StudioImportKind | null {
  const extension = extensionOf(name);
  if (IMAGE_EXTENSIONS.has(extension)) return 'image';
  if (VIDEO_EXTENSIONS.has(extension)) return 'video';
  return null;
}

/**
 * 上传接口返回的是服务端绝对路径（`.../backend/code/result/uploads/x.png`）。
 * 画布只保存 `result/...` 相对路径，媒体路由会把它映射到 `/code/`。
 * 不在 result/ 下的路径一律拒绝，避免引用临时文件或越界路径。
 */
export function toCanvasMediaPath(filePath: string): string | null {
  const clean = String(filePath || '').replace(/\\/g, '/').trim();
  if (!clean) return null;
  const mounted = clean.indexOf('/code/');
  if (mounted >= 0) {
    const relative = clean.slice(mounted + '/code/'.length);
    return relative.startsWith('result/') ? relative : null;
  }
  const direct = clean.lastIndexOf('result/');
  if (direct >= 0) return clean.slice(direct);
  return null;
}

function titleFor(filename: string): string {
  const base = String(filename || '').replace(/\\/g, '/').split('/').pop() || '';
  const dot = base.lastIndexOf('.');
  const stem = (dot > 0 ? base.slice(0, dot) : base).replace(UPLOAD_PREFIX, '').trim();
  return stem || '导入素材';
}

export function describeImportedMedia(filename: string, filePath: string): StudioImportResult | null {
  const kind = importKindForName(filename);
  const mediaPath = toCanvasMediaPath(filePath);
  if (!kind || !mediaPath) return null;
  return { kind, title: titleFor(filename), mediaPath };
}
