import type { ProductionImageShot } from './plannerMediaTypes';

export const mediaGenerationLabels = { not_started: '尚未开始', reserved: '已预留，等待提交', submitting: '正在提交', unknown: '提交结果未确认', accepted: '生成请求已受理', rejected: '生成请求被拒绝' };
export const mediaCompletionLabels = { not_observed: '尚未查询结果', pending: '等待生成结果', completed: '已确认生成完成', failed: '已确认生成失败', unknown: '生成结果未确认' };
export const mediaReceptionLabels = { not_enabled: '未启用自动保存', not_started: '尚未接收', receiving: '接收结果待核对', saved: '有历史保存记录', failed: '保存失败', unknown: '保存结果未确认' };
export const mediaFileLabels = { not_recorded: '尚无文件记录', not_verified: '当前文件未核验', not_published: '已接收，尚未完成耐久保存', available: '本次核验可用', missing: '当前文件缺失', invalid: '当前文件校验失败' };
export const mediaAuthorityLabels = { not_applicable: '未启用此保存权限', valid: '保存权限有效', expired: '保存权限已到期', revoked: '保存权限已撤销', unavailable: '保存权限未确认' };
export const mediaErrorLabels: Record<NonNullable<ProductionImageShot['error_code']>, string> = {
  HTTP_NOT_200: '资源获取未成功，原因尚未确认。', NETWORK_OR_WORKER: '接收过程未确认，需要核对已有记录。', INVALID_BYTES: '图片完整性校验未通过。',
  LOCAL_STORAGE: '本地保存未完成。', INTERRUPTED_MISSING_BYTES: '接收中断，缺少可核验的完整文件。', RESOURCE_LIMIT: '接收超出资源限制。', TRUNCATED_RESPONSE: '接收的图片不完整。',
  TIMEOUT: '接收超时，结果待确认。', CAPACITY_EXCEEDED: '本地保存容量已达限制。', AUTHORITY_EXPIRED: '保存权限已到期。', AUTHORITY_REVOKED: '保存权限已撤销。',
  AUTHORITY_UNAVAILABLE: '保存权限暂不可核验。', LEGACY_FILE_NOT_VERIFIED: '旧版交付已登记，当前文件未核验。', FILE_MISSING: '历史记录保留，当前文件缺失。',
  FILE_INVALID: '历史记录保留，当前文件校验失败。', PERSISTENCE_PENDING: '完整保存尚未确认。', DELIVERY_PENDING: '图片已保存，正式登记尚未完成。',
};
export function mediaShotPresentation(shot: ProductionImageShot, current: boolean): { label: string; detail: string; tone: 'neutral' | 'attention' | 'saved' } {
  if (!current) return { label: '结果待确认', detail: '以下为上次读取记录，当前结果尚未核验。', tone: 'neutral' };
  const attention = (detail: string) => ({ label: '结果待确认', detail, tone: 'attention' as const });
  if (shot.generation === 'unknown' || shot.completion === 'unknown' || shot.reception === 'unknown') return attention('存在未确认结果，不能据此认定生成或保存成功。');
  if (shot.generation === 'rejected' || shot.completion === 'failed') return { label: '生成失败', detail: '已有记录确认生成未成功。', tone: 'attention' };
  if (shot.reception === 'failed') return { label: '保存失败', detail: shot.http_status === 404 ? '资源获取返回 404，原因尚未确认。不会自动重试。' : shot.error_code ? mediaErrorLabels[shot.error_code] : '保存未成功，需要核对已有记录。', tone: 'attention' };
  if (shot.delivery_origin === 'legacy') return attention('旧版交付已登记，当前文件未核验。');
  if (shot.file_availability === 'missing' || shot.file_availability === 'invalid') return attention(mediaFileLabels[shot.file_availability] + '，历史登记事实仍保留。');
  if (shot.file_availability === 'available' && (shot.reception !== 'saved' || !shot.historical_saved)) return attention('图片文件本次核验可用，保存记录或正式登记尚未完成。');
  if (shot.file_availability === 'available' && shot.persisted === true && shot.delivery_registered && shot.delivery_origin === 'managed' && shot.completion === 'completed' && shot.reception === 'saved' && shot.historical_saved && !shot.needs_input && shot.error_code === null) {
    return { label: '已保存', detail: '图片文件本次核验可用，已完成正式登记。', tone: 'saved' };
  }
  if (shot.persisted === true && !shot.delivery_registered) return attention('图片文件已保存，正式登记尚未完成。');
  if (shot.completion === 'completed' && !shot.historical_saved && ['not_enabled', 'not_started', 'receiving'].includes(shot.reception)) {
    return { label: '已生成待保存', detail: shot.opt_in ? '生成完成，完整保存与正式登记尚未确认。' : '生成完成，此任务未启用自动保存。', tone: shot.needs_input ? 'attention' : 'neutral' };
  }
  if (shot.needs_input) return attention(shot.error_code ? mediaErrorLabels[shot.error_code] : '需要核对已有记录；本页仅展示状态。');
  if (shot.generation === 'not_started') return { label: '尚未生成', detail: '此镜头尚无生成请求。', tone: 'neutral' };
  if (shot.generation === 'reserved') return { label: '等待生成', detail: '已预留生成意图，尚未确认提交。', tone: 'neutral' };
  if (shot.generation === 'submitting' || shot.generation === 'accepted' && ['not_observed', 'pending'].includes(shot.completion)) return { label: '生成中', detail: '等待此镜头的生成结果，不代表图片已保存。', tone: 'neutral' };
  return attention('当前记录不足以确认完整保存与正式登记。');
}
