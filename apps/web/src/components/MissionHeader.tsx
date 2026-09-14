"use client";

import Image from "next/image";
import Link from "next/link";
import clsx from "clsx";

import { useCountdown } from "@/hooks/useMission";
import { MISSION_LABEL, countdown, deadline } from "@/lib/format";
import type { MissionSummary } from "@/lib/types";
import { StatusChip, type Tone } from "./primitives";

export const missionTone = (status: string): Tone => {
  if (status === "recovered") return "secure";
  if (status === "escalated" || status === "failed") return "breach";
  if (status === "replanning") return "caution";
  if (status === "approval_required") return "caution";
  return "signal";
};

/**
 * Persistent mission header (DOCUMENT 04 §10): the business consequence and the
 * clock stay on screen for the whole demo.
 */
export function MissionHeader({
  mission,
  risk,
  connection,
}: {
  mission: MissionSummary;
  risk: string;
  connection: "connecting" | "live" | "closed";
}) {
  const remaining = useCountdown(mission.deadline.remaining_seconds);
  const tone = missionTone(mission.status);
  const tight = remaining < 900 && !["recovered", "escalated"].includes(mission.status);

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-deep/95 backdrop-blur">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-8 gap-y-3 px-5 py-3">
        <Link href="/" className="flex items-center gap-3 shrink-0">
          <Image src="/icon-phoneops.png" alt="" width={26} height={26} priority />
          <span className="sr-only">PHONEOPS AI</span>
        </Link>

        <div className="flex items-baseline gap-3">
          <span className="text-lg font-600 tracking-tight">{mission.entity_ref}</span>
          <StatusChip tone={tone} live={!["recovered", "escalated", "failed"].includes(mission.status)}>
            {MISSION_LABEL[mission.status]}
          </StatusChip>
        </div>

        <div className="hidden min-w-0 flex-1 md:block">
          <p className="label">Risk</p>
          <p className="truncate text-sm text-mist">{risk}</p>
        </div>

        <div className="flex items-center gap-8">
          <div>
            <p className="label">Cutoff</p>
            <p className="reading text-sm">
              {deadline(mission.deadline.cutoff_at, mission.deadline.timezone)}
            </p>
          </div>
          <div>
            <p className="label">Time remaining</p>
            <p
              className={clsx(
                "reading text-xl font-500 tabular-nums",
                mission.deadline.expired || tight ? "text-breach" : "text-chalk",
              )}
            >
              {countdown(remaining)}
            </p>
          </div>
          <span
            className={clsx(
              "hidden h-2 w-2 rounded-full lg:block",
              connection === "live"
                ? "bg-secure animate-pulse-dot"
                : connection === "connecting"
                  ? "bg-caution"
                  : "bg-dust",
            )}
            title={`Mission stream ${connection}`}
          />
        </div>
      </div>
    </header>
  );
}
