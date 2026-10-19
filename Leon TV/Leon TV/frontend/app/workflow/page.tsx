import { Suspense } from 'react';
import WorkflowPanel from '@/components/WorkflowPanel';

export default function WorkflowPage() {
  return <Suspense fallback={<p className="p-8 text-sm text-gray-500">正在打开六阶段工作流…</p>}><WorkflowPanel /></Suspense>;
}
