'use client';

import CreatorFrame from './CreatorFrame';
import { useEffect, useMemo, useRef, useSyncExternalStore, type MouseEvent } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { FileText, Film, Loader2, Plus, RefreshCw, ShieldCheck } from 'lucide-react';
import CreatorBriefEditor from './CreatorBriefEditor';
import { CreatorBriefController } from '@/lib/creatorBriefController';
import type { CreatorBriefDetail, CreatorEditorFields } from '@/lib/creatorBriefTypes';

export default function CreatorWorkspace() {
  const router = useRouter();
  const search = useSearchParams();
  const requestedId = search.get('brief');
  const controller = useMemo(() => new CreatorBriefController(() => window.localStorage), []);
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getServerSnapshot);
  const errorsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let active = true;
    void controller.initialize(requestedId).then(() => {
      const document = controller.getSnapshot().document;
      if (active && !requestedId && document) router.replace(`/creator?brief=${encodeURIComponent(document.id)}`);
    });
    return () => { active = false; controller.invalidate(); };
  }, [controller, requestedId, router]);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (state.dirty || state.saving || state.pending) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [state.dirty, state.saving, state.pending]);
  useEffect(() => { if (Object.keys(state.fieldErrors).length) errorsRef.current?.focus(); }, [state.fieldErrors]);

  const confirmDiscard = () => !state.dirty || window.confirm('当前有未保存编辑。确定放弃这些编辑并打开其他内容？');
  const navigate = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    if ((state.dirty || state.saving || state.pending) && !window.confirm('未保存的正文不会保存在浏览器。原请求键会保留以便查回。确定离开？')) event.preventDefault();
  };
  const show = (detail: CreatorBriefDetail | null) => {
    if (detail && controller.getSnapshot().document?.id === detail.id) router.replace(`/creator?brief=${encodeURIComponent(detail.id)}`);
  };
  const save = () => { void controller.save().then(show).catch(error => controller.report(error)); };
  const recover = (retry: boolean) => { void controller.recover(retry).then(show).catch(error => controller.report(error)); };
  const errorLabels: Record<keyof CreatorEditorFields, { label: string; id: string }> = {
    title: { label: '作品名称', id: 'creator-title' }, script: { label: '剧本', id: 'creator-script' },
    requirements: { label: '制作要求', id: 'creator-requirements' }, budgetText: { label: '预算', id: 'creator-budget' },
  };
  const document = state.document;
  return (
    <CreatorFrame onNavigate={navigate} navigation={<nav className="creator-brief-nav" aria-label="我的创作简报">
      <button type="button" className="creator-primary" disabled={state.saving || Boolean(state.pending)} onClick={() => {
        if (!confirmDiscard()) return;
        try { controller.newDraft(); router.replace('/creator'); } catch (error) { controller.report(error); }
      }}><Plus size={16} />新建简报</button>
      <div className="creator-list-heading"><span>我的简报</span><button type="button" aria-label="刷新简报列表" disabled={state.listLoading} onClick={() => void controller.loadList()}><RefreshCw size={13} /></button></div>
      {state.listLoading && !state.briefs.length && <p className="creator-muted" role="status">正在读取简报…</p>}
      {!state.listLoading && !state.briefs.length && <p className="creator-muted">{state.error ? '列表暂不可用，可稍后刷新。' : '还没有已保存的简报。'}</p>}
      {state.briefs.map(brief => <button key={brief.id} type="button" className="creator-brief-item" aria-current={document?.id === brief.id ? 'page' : undefined} disabled={state.saving} onClick={() => {
        if (document?.id === brief.id || !confirmDiscard()) return;
        void controller.open(brief.id).then(show);
      }}><FileText size={16} /><span><strong>{brief.title}</strong><small>v{brief.revision} · {brief.budget_cny === null ? '预算待定' : `¥${brief.budget_cny} 草稿`}</small></span></button>)}
      {state.nextOffset !== null && <button type="button" className="creator-text-button" disabled={state.listLoading} onClick={() => void controller.loadList(true)}>加载更多简报</button>}
    </nav>}>
      <header className="creator-heading"><div><p className="creator-eyebrow">YOUR NEXT FILM STARTS HERE</p><h1>从这一页剧本，<br />开始一部短片。</h1><p>先留下故事、制作要求和预算草稿。保存不等于开始制作。</p></div><span className="creator-badge">创作简报 · 不授权</span></header>
      <div className="creator-current"><span>{document ? `${document.title} · v${document.revision}` : '新的创作简报'}</span><span className="creator-muted">{state.loading ? '正在读取…' : state.dirty ? '有未保存编辑' : document ? '已保存' : '尚未保存'}</span>{document && <button type="button" className="creator-text-button" disabled={state.saving || state.loading} onClick={() => recover(false)}><RefreshCw size={13} />查回 / 刷新</button>}</div>
      {state.loading && <p className="creator-notice" role="status"><Loader2 size={15} className="animate-spin" />正在查回简报，已有输入保持不变…</p>}
      {state.notice && <p className="creator-notice" role="status">{state.notice}</p>}
      {state.error && <p className="creator-notice creator-notice-error" role="alert">{state.error} 当前编辑不会被清空。</p>}
      {Object.keys(state.fieldErrors).length > 0 && <div className="creator-notice creator-notice-error" role="alert" ref={errorsRef} tabIndex={-1}><strong>请检查以下内容</strong><ul>{Object.entries(state.fieldErrors).map(([key, message]) => <li key={key}><a href={`#${errorLabels[key as keyof CreatorEditorFields].id}`}>{errorLabels[key as keyof CreatorEditorFields].label}：{message}</a></li>)}</ul></div>}
      {state.pending && <section className="creator-notice" aria-label="原保存结果待确认"><h2>原保存结果待确认</h2><p>保留原请求键，只查回或原样重试，不换键新建。正文不写入浏览器；未保存编辑请留在当前页。</p><div className="creator-action-row"><button type="button" className="creator-secondary" disabled={state.saving || state.loading} onClick={() => recover(false)}>查询原保存</button><button type="button" className="creator-secondary" disabled={state.saving || state.loading} onClick={() => recover(true)}>按原键确认 / 重试</button></div></section>}
      {state.conflictDetected && <section className="creator-notice" aria-label="版本冲突"><h2>服务器版本已更新，你的修改仍保留</h2><p>{state.conflict ? `编辑基于 v${document?.revision ?? '?'}，服务器为 v${state.conflict.revision}。请核对后选择，不会自动覆盖或提交。` : '暂未读到最新版本。请查回服务器内容后再合并。'}</p>
        {state.conflict && <><details className="creator-conflict-details"><summary>对照服务器当前版本</summary><p>作品名称：{state.conflict.title}</p><label className="creator-label" htmlFor="creator-server-script">服务器剧本（只读）</label><textarea id="creator-server-script" className="creator-field" rows={5} readOnly value={state.conflict.script} /><p>制作要求：{state.conflict.requirements || '未填写'}<br />预算：{state.conflict.budget_cny === null ? '待定' : `¥${state.conflict.budget_cny}（草稿）`}</p></details><div className="creator-action-row"><button type="button" className="creator-secondary" onClick={() => controller.rebase(true)}>保留我的修改，以当前版本为基础</button><button type="button" className="creator-text-button" onClick={() => { if (window.confirm('确定放弃本地修改，载入服务器版本？')) controller.rebase(false); }}>放弃本地修改并载入服务器版本</button></div></>}
      </section>}
      <div className="creator-content-grid">
        <CreatorBriefEditor fields={state.fields} onChange={patch => controller.edit(patch)} onSave={save} busy={state.saving} disabled={state.loading || Boolean(state.pending) || state.conflictDetected || Boolean(document && !document.is_current) || Boolean(document && !state.dirty)} readOnly={state.loading} revision={document?.revision} dirty={state.dirty} fieldErrors={state.fieldErrors} />
        <aside className="creator-right">
          <section className="creator-panel creator-panel-body"><h2>成片还没有开始</h2><div className="creator-media-empty"><Film size={33} strokeWidth={1.2} /><p>未来成片将在这里呈现</p><span>真实制作与看片待接入</span></div><p className="creator-muted">保存简报不会生成视频。已有画布和六阶段可从高级入口打开。</p></section>
          <section className="creator-panel creator-panel-body"><h2>先把创作范围留下</h2><ol className="creator-scope"><li><span>01</span><div>保存创作简报<p>剧本、要求与预算草稿</p></div></li><li><span>02</span><div>Agent 规划与制作<p>待接入，不会自动开始</p></div></li><li><span>03</span><div>核验与最终看片<p>待接入，不预先接受</p></div></li></ol><p className="creator-muted creator-safety"><ShieldCheck size={15} />预算是草稿；当前没有制作授权。</p></section>
          {document && <section className="creator-panel creator-panel-body"><h2>保存记录</h2><dl className="creator-record"><div><dt>当前简报</dt><dd>v{document.revision}</dd></div><div><dt>来源版本</dt><dd>v{document.source_version}</dd></div><div><dt>正文字符</dt><dd>{document.script_characters}</dd></div><div><dt>预算草稿</dt><dd>{document.budget_cny === null ? '待定' : `¥${document.budget_cny}`}</dd></div><div><dt>制作权限</dt><dd>未授权 · 不执行</dd></div></dl><details className="creator-conflict-details"><summary>查看版本记录</summary>{document.versions.map(version => <p key={version.revision}>简报 v{version.revision} · 来源 v{version.source_version}<br /><time dateTime={version.saved_at}>{new Date(version.saved_at).toLocaleString('zh-CN')}</time></p>)}</details></section>}
        </aside>
      </div>
    </CreatorFrame>
  );
}
