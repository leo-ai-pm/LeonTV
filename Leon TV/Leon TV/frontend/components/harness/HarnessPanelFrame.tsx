'use client';

import { useEffect, useId, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';

/** Native dialog supplies keyboard focus containment without adding a UI library. */
export default function HarnessPanelFrame({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = dialogRef.current;
    dialog?.showModal();
    return () => { dialog?.close(); };
  }, []);

  return (
    <dialog ref={dialogRef} aria-labelledby={titleId} className="harness-dialog" onCancel={event => { event.preventDefault(); onClose(); }}>
      <header className="harness-heading">
        <div><span className="harness-mode">测试模式 · 零付费</span><h2 id={titleId}>运行验证</h2></div>
        <button type="button" onClick={onClose} className="studio-icon-button" aria-label="关闭运行验证"><X size={19} /></button>
      </header>
      <p className="harness-boundary">本阶段验证运行、恢复与测试终审。模型循环与真实生产尚未接入，不生成真实成片。</p>
      <div className="harness-content">{children}</div>
    </dialog>
  );
}
