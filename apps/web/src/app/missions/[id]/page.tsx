import { MissionControl } from "@/components/MissionControl";

export default async function MissionPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ autostart?: string }>;
}) {
  const { id } = await params;
  const { autostart } = await searchParams;
  return <MissionControl missionId={id} autostart={autostart === "1"} />;
}
