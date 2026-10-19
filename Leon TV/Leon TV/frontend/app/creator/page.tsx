import { Suspense } from 'react';
import CreatorWorkspace from '@/components/creator/CreatorWorkspace';

export default function CreatorPage() {
  return <Suspense fallback={<div className="creator-loading">正在打开创作简报…</div>}><CreatorWorkspace /></Suspense>;
}
