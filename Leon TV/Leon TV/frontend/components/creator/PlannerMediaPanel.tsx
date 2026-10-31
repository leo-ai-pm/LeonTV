'use client';

import { useEffect, useId, useMemo, useSyncExternalStore } from 'react';
import { ImageIcon, RefreshCw } from 'lucide-react';
import type { PlannerDetail } from '@/lib/briefPlannerTypes';
import { PlannerMediaController, sameMediaBinding } from '@/lib/plannerMediaController';
import { mediaAuthorityLabels, mediaCompletionLabels, mediaErrorLabels, mediaFileLabels, mediaGenerationLabels, mediaReceptionLabels, mediaShotPresentation } from '@/lib/plannerMediaView';

export default function PlannerMediaPanel({ task }: { task: PlannerDetail }) {
  const heading = useId();
  const controller = useMemo(() => new PlannerMediaController(), []);
  const binding = useMemo(() => ({ task_id: task.id, source_snapshot_hash: task.source.snapshot_hash }), [task.id, task.source.snapshot_hash]);
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getServerSnapshot);
  useEffect(() => { void controller.select(binding); }, [controller, binding]);
  useEffect(() => () => controller.invalidate(), [controller]);
  // Filter at render time too: an effect has not necessarily run on the first new-task render.
  const bound = sameMediaBinding(state.binding, binding);
  const data = bound ? state.data : null;
  const reading = !bound || state.phase === 'loading' || state.phase === 'refreshing';
  const current = bound && state.phase === 'ready';
  const images = data?.production_images;
  const error = bound ? state.error : '';
  const noRecord = bound && state.phase === 'not_found';
  return <section className="creator-execution creator-media" aria-labelledby={heading} aria-busy={reading}>
    <header className="creator-execution-heading">
      <div><h3 id={heading}><ImageIcon size={17} aria-hidden="true" />分镜图片</h3><p className="creator-muted">查看当前任务的生成与保存记录。</p></div>
      <button type="button" className="creator-secondary" aria-disabled={reading} onClick={() => {
        if (reading) return;
        void controller.refresh();
      }}><RefreshCw size={14} aria-hidden="true" />刷新图片记录</button>
    </header>
    <p className="creator-muted" role="status" aria-atomic="true">{reading ? data ? '正在重新核验图片记录，上次结果暂不作为当前状态。' : '正在读取分镜图片记录…' : error ? '本次读取失败，当前图片状态未核验。' : noRecord ? '尚无执行记录。' : images?.status === 'not_configured' ? '尚未配置正式分镜图片范围，未取得图片制作授权。' : images ? `共 ${images.required_count} 镜，本次文件核验可用 ${images.verified_file_count} 镜，历史登记 ${images.registered_count} 镜。` : '图片记录尚未读取。'}</p>
    {error && <p role="alert" className="creator-notice creator-notice-error">{error}{data && ' 下方仅保留上次读取内容。'}</p>}
    {noRecord && <p className="creator-muted">此规划任务尚无执行记录。读取不会创建记录或开始制作。</p>}
    {images && <div data-stale={!current ? 'true' : undefined}>
      {images.evidence_kind && <p className="creator-notice">{images.evidence_kind === 'synthetic' ? '合成验证记录 · 不代表真实图片生成或实际费用。' : '真实执行回执记录 · 影像质量尚未审核。'}{!current && ' 上次读取，当前未核验。'}</p>}
      {images.needs_input_count > 0 && current && <p className="creator-muted">{images.needs_input_count} 镜的结果或保存过程需要核对。本页只读。</p>}
      {images.status !== 'not_configured' && images.shots.length === 0 && <p className="creator-muted">当前范围暂无正式镜头记录。</p>}
      <ol className="creator-media-shots">{images.shots.map(shot => {
        const presentation = mediaShotPresentation(shot, current);
        return <li key={`${shot.shot_id}:${shot.first_frame_version}`} className="creator-media-shot" data-tone={presentation.tone}>
          <header><h4>{shot.shot_id}<span className="creator-muted">首帧 v{shot.first_frame_version}</span></h4><span className="creator-badge">{presentation.label}</span></header>
          <p>{presentation.detail}</p>
          <details className="creator-conflict-details"><summary>查看此镜记录{!current && '（上次读取，未核验）'}</summary>
            {!current && <p className="creator-muted">以下均为上次读取值，不能证明文件当前可用或任务仍在运行。</p>}
            <dl className="creator-record">
              <div><dt>生成请求</dt><dd>{mediaGenerationLabels[shot.generation]}</dd></div>
              <div><dt>生成结果</dt><dd>{mediaCompletionLabels[shot.completion]}</dd></div>
              <div><dt>保存过程</dt><dd>{mediaReceptionLabels[shot.reception]}</dd></div>
              <div><dt>{current ? '当前文件' : '上次文件核验'}</dt><dd>{current ? mediaFileLabels[shot.file_availability] : shot.file_availability === 'available' ? '上次可用；本次未核验' : mediaFileLabels[shot.file_availability]}</dd></div>
              <div><dt>完整接收 / 耐久保存</dt><dd>{shot.received === null ? '未核验' : shot.received ? '有核验记录' : '未确认取得'} / {shot.persisted === null ? '未核验' : shot.persisted ? '有核验记录' : '未确认保存'}</dd></div>
              <div><dt>历史正式登记</dt><dd>{shot.delivery_registered ? shot.delivery_origin === 'legacy' ? '旧版交付已登记，当前文件未核验' : '已登记' : '尚未登记'}</dd></div>
              <div><dt>历史保存</dt><dd>{shot.historical_saved ? '有保存记录；当前可用性另行核验' : '无新保存链记录'}</dd></div>
              <div><dt>自动保存范围</dt><dd>{shot.opt_in ? '已明确启用此镜保存' : '未启用'}</dd></div>
              <div><dt>接收权限</dt><dd>{mediaAuthorityLabels[shot.authority]}；不代表可在此执行</dd></div>
              <div><dt>费用观察</dt><dd>{shot.fee_observed ? '已有平台费用观察，非最终账单' : '费用尚未确认，不代表零费用'}</dd></div>
              {shot.http_status !== null && <div><dt>接收 HTTP 状态</dt><dd>{shot.http_status}{shot.http_status === 404 && ' · 原因尚未确认'}</dd></div>}
              {shot.error_code && <div><dt>需核对事项</dt><dd>{mediaErrorLabels[shot.error_code]}</dd></div>}
              <div><dt>影像质量 / 最终人验</dt><dd>未审核 / 待进行</dd></div>
              <div><dt>操作 / 查询引用</dt><dd><code>{shot.operation_id ?? '尚无操作'} / {shot.query_id ?? '尚无查询'}</code></dd></div>
            </dl>
          </details>
        </li>;
      })}</ol>
      <details className="creator-conflict-details"><summary>费用与读取说明{!current && '（上次记录）'}</summary>
        <dl className="creator-record"><div><dt>媒体累计保留风险</dt><dd>¥{data.media.retained_exposure_cny}，不是消费</dd></div><div><dt>平台已观察费用</dt><dd>{data.media.platform_observed_cny === null ? '未确认，不代表零费用' : `¥${data.media.platform_observed_cny}，非最终账单`}</dd></div><div><dt>风险释放 / 最终账单</dt><dd>未释放 / 未确认</dd></div><div><dt>页面读取时间</dt><dd>{state.observedAt ? <time dateTime={state.observedAt}>{new Date(state.observedAt).toLocaleString('zh-CN')}</time> : '尚未读取'}{!current && ' · 当前未核验'}</dd></div></dl>
        <p className="creator-muted">此面板只读取本地记录，不生成、下载或恢复图片。图片保存和登记不代表视频完成、视觉合格或最终验收通过。</p>
      </details>
    </div>}
  </section>;
}
