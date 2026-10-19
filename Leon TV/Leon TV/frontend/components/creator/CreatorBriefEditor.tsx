'use client';

import { FileText, Save, ShieldCheck } from 'lucide-react';
import type { CreatorEditorFields } from '@/lib/creatorBriefTypes';

export default function CreatorBriefEditor({ fields, onChange, onSave, busy, disabled, readOnly, revision, dirty, fieldErrors }: {
  fields: CreatorEditorFields;
  onChange: (patch: Partial<CreatorEditorFields>) => void;
  onSave: () => void;
  busy: boolean; disabled: boolean; readOnly?: boolean; revision?: number; dirty: boolean;
  fieldErrors?: Partial<Record<keyof CreatorEditorFields, string>>;
}) {
  return (
    <form className="creator-panel creator-editor" noValidate onSubmit={event => { event.preventDefault(); onSave(); }}>
      <div className="creator-section-head"><h2><FileText size={16} />故事从这里开始</h2><span className="creator-muted">{revision ? `已保存版本 v${revision}` : '新的简报'}{dirty ? ' · 有未保存编辑' : ''}</span></div>
      <div className="creator-panel-body">
        <label className="creator-label" htmlFor="creator-title">作品名称</label>
        <input id="creator-title" className="creator-field" value={fields.title} readOnly={readOnly} onChange={event => onChange({ title: event.target.value })} placeholder="未命名创作简报" aria-invalid={Boolean(fieldErrors?.title)} aria-describedby="creator-title-error" />
        <p id="creator-title-error" className="creator-field-error">{fieldErrors?.title}</p>
        <label className="creator-label" htmlFor="creator-script">剧本或故事梗概</label>
        <textarea id="creator-script" className="creator-field creator-script" readOnly={readOnly} value={fields.script} onChange={event => onChange({ script: event.target.value })} placeholder="粘贴你的剧本，或先写下人物、发生的变化与想留下的画面……" aria-invalid={Boolean(fieldErrors?.script)} aria-describedby="creator-script-error creator-script-hint" />
        <p id="creator-script-error" className="creator-field-error">{fieldErrors?.script}</p>
        <div className="creator-editor-meta"><p id="creator-script-hint">正文只在编辑器和本机后端保存，不写入浏览器存储。</p><span>{Array.from(fields.script).length} 字符</span></div>
      </div>
      <section className="creator-editor-section" aria-labelledby="creator-requirements-heading">
        <h2 id="creator-requirements-heading">你希望它怎样呈现</h2>
        <div className="creator-specs"><div><span>视觉方向</span><strong>写实影视</strong></div><div><span>画面比例</span><strong>9:16 竖屏</strong></div><div><span>目标时长</span><strong>15 秒</strong></div></div>
        <label className="creator-label" htmlFor="creator-requirements">补充制作要求 <span>可选</span></label>
        <textarea id="creator-requirements" className="creator-field" rows={3} readOnly={readOnly} value={fields.requirements} onChange={event => onChange({ requirements: event.target.value })} placeholder="例如：自然光线，情绪克制；不要添加旁白。" aria-invalid={Boolean(fieldErrors?.requirements)} aria-describedby="creator-requirements-error" />
        <p id="creator-requirements-error" className="creator-field-error">{fieldErrors?.requirements}</p>
      </section>
      <section className="creator-editor-section" aria-labelledby="creator-budget-heading">
        <div className="creator-inline"><h2 id="creator-budget-heading">预算上限草稿</h2><span className="creator-badge">未授权</span></div>
        <div className="creator-budget-row"><div><label className="creator-label" htmlFor="creator-budget">金额（元）<span>可留空</span></label><input id="creator-budget" className="creator-field creator-money" readOnly={readOnly} inputMode="decimal" autoComplete="off" value={fields.budgetText} onChange={event => onChange({ budgetText: event.target.value })} placeholder="待定" aria-invalid={Boolean(fieldErrors?.budgetText)} aria-describedby="creator-budget-error creator-budget-hint" /><p id="creator-budget-error" className="creator-field-error">{fieldErrors?.budgetText}</p></div><p id="creator-budget-hint" className="creator-muted">留空表示预算待定，不是 0 元报价。填写金额和保存都不构成付费授权。</p></div>
      </section>
      <footer className="creator-editor-footer"><p><ShieldCheck size={15} />仅保存创作简报，不创建运行、不调用模型。</p><button type="submit" className="creator-primary" disabled={busy || disabled}><Save size={16} />{busy ? '正在确认保存…' : revision ? '保存新版本' : '保存简报'}</button></footer>
    </form>
  );
}
