import { Suspense } from 'react';
import { redirect } from 'next/navigation';
import WorkflowPanel from '@/components/WorkflowPanel';
import { isLegacyWorkflowQuery } from '@/lib/entryRouting';

function Loading() {
  return (
    <div className="flex items-center justify-center h-screen bg-gray-50">
      <div className="text-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto mb-4"></div>
        <p className="text-gray-500">加载中...</p>
      </div>
    </div>
  );
}

export default async function Home({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  if (!isLegacyWorkflowQuery(await searchParams)) redirect('/creator');
  return (
    <Suspense fallback={<Loading />}>
      <WorkflowPanel />
    </Suspense>
  );
}
