"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import clsx from "clsx";

import { api } from "@/lib/api";
import { countdown, deadline } from "@/lib/format";
import { useCountdown } from "@/hooks/useMission";
import type { HealthOut, MissionSummary } from "@/lib/types";

/**
 * Screen 01 — Critical Exception (DOCUMENT 04 §5).
 *
 * Urgency, not panic: one shipment, one consequence, one clock, one action.
 */
export function CriticalException() {
  const router = useRouter();
  const [mission, setMission] = useState<MissionSummary | null>(null);
  const [health, setHealth] = useState<HealthOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [created, status] = await Promise.all([
          api.createFlagship({ reset: true }),
          api.health(),
        ]);
        if (!alive) return;
        setMission(created);
        setHealth(status);
      } catch (err) {
        // "Start the API on port 8000" is a developer's instruction, and it was
        // what a deployed site showed its visitors. Report what actually failed.
        if (alive) {
          setError(
            err instanceof Error && err.message
              ? err.message
              : "The recovery engine is not responding.",
          );
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const remaining = useCountdown(mission?.deadline.remaining_seconds);

  const startRecovery = async () => {
    if (!mission) return;
    setStarting(true);
    // Open the mission, do not run it. The brief and its open questions are
    // the next beat: the recovery is blocked on information no connected system
    // holds. Starting from here skipped straight past that, which also made the
    // demo script's "open without autostart" instruction impossible to follow.
    router.push(`/missions/${mission.id}`);
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-[1100px] flex-col px-6 py-10">
      <div className="flex items-center justify-between">
        <Image
          src="/logo-phoneops.png"
          alt="PHONEOPS AI"
          width={196}
          height={66}
          priority
          className="h-9 w-auto"
        />
        {health && (
          <p className="reading text-[11px] tracking-[0.12em] text-dust">
            CALL-E {health.calle_mode.toUpperCase()} · v{health.version}
          </p>
        )}
      </div>

      {error && (
        <p className="mt-24 border-l-2 border-breach pl-4 text-breach">{error}</p>
      )}

      {mission && (
        <section className="mt-16 border border-line bg-panel">
          <div className="h-1 w-full bg-breach" />
          <div className="px-8 py-9 sm:px-10 sm:py-12">
            <p className="reading text-[11px] tracking-[0.2em] text-breach">CRITICAL EXCEPTION</p>

            <h1 className="mt-5 text-5xl font-700 tracking-tight sm:text-6xl">
              {mission.entity_ref}
            </h1>
            <p className="mt-2 text-xl text-mist">Assigned carrier cancelled the pickup</p>

            <dl className="mt-12 grid gap-x-12 gap-y-8 sm:grid-cols-3">
              <div className="sm:col-span-3">
                <dt className="label">Impact</dt>
                <dd className="mt-1 max-w-[46ch] text-lg leading-snug">
                  Shipment may miss tonight&apos;s departure
                </dd>
              </div>
              <div>
                <dt className="label">Pickup cutoff</dt>
                <dd className="reading mt-1 text-3xl font-500">
                  {deadline(mission.deadline.cutoff_at, mission.deadline.timezone)}
                </dd>
              </div>
              <div>
                <dt className="label">Time remaining</dt>
                <dd
                  className={clsx(
                    "reading mt-1 text-3xl font-500 tabular-nums",
                    remaining < 900 ? "text-breach" : "text-chalk",
                  )}
                >
                  {countdown(remaining)}
                </dd>
              </div>
              <div>
                <dt className="label">Severity</dt>
                <dd className="mt-1 text-3xl font-600 text-breach">CRITICAL</dd>
              </div>
            </dl>

            <button
              type="button"
              onClick={startRecovery}
              disabled={starting}
              className="mt-12 w-full border border-signal bg-signal px-6 py-4 text-base font-600 tracking-wide text-white transition-colors hover:bg-signal-dim disabled:opacity-60 sm:w-auto sm:px-12"
            >
              {starting ? "Opening recovery mission…" : "Open recovery mission"}
            </button>

            <p className="mt-5 max-w-[64ch] text-sm leading-relaxed text-dust">
              No connected system knows which carrier can still collect this shipment before the
              cutoff. That answer only exists with the dispatchers, on the phone.
            </p>
          </div>
        </section>
      )}

      <footer className="mt-auto pt-16">
        <p className="text-sm text-dust">When the plan breaks, PHONEOPS calls, learns and recovers.</p>
      </footer>
    </main>
  );
}
