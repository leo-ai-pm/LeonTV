import WorkspaceShell from '@/components/workspace/WorkspaceShell';

export default async function WorkspaceProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <WorkspaceShell projectId={id} />;
}
