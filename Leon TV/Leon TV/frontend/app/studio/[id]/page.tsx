import StudioCanvas from '@/components/studio/StudioCanvas';

export default async function StudioCanvasPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <StudioCanvas key={id} projectId={id} />;
}
