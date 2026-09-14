"use client";

import { TriangleAlert } from "lucide-react";

import { operatorSymbol, readValue } from "@/lib/format";
import type { RecoveryView, StreamEvent } from "@/lib/types";

/**
 * THE hero moment (DOCUMENT 04 §17). The strategy must not fail quietly — the
 * judge has to experience it. Shown for ~3 seconds, then the control room
 * returns with the strategy diff already in place.
 */
export function InvalidationOverlay({
  event,
  view,
}: {
  event: StreamEvent;
  view: RecoveryView | null;
}) {
  const payload = event.payload as Record<string, unknown>;
  const diff = view?.strategy_diff;
  const replan = view?.latest_replan;

  const target =
    (payload.target as string) ?? diff?.before?.target ?? view?.mission.current_strategy_target ?? "";
  const required =
    (payload.required as string) ??
    readValue(replan?.required_value ?? diff?.required_value ?? null);
  const observed =
    (payload.observed as string) ??
    readValue(replan?.observed_value ?? diff?.observed_value ?? null);
  // The event carries the violated constraint's id, so the operator and the
  // wording come from the mission's own constraint rather than a guess.
  //
  // The engine's phrasing is precise for the audit trail ("Required pickup_time
  // <= 17:30; observed 18:00 -> VIOLATED") but it is machine-speak, and the two
  // readings below already say it. This states the operational consequence.
  const constraint = view?.constraints.find((item) => item.id === event.constraint_id);
  const operator = constraint?.operator ?? "lte";
  const rule = constraint?.short_label ?? "";

  // A plan can also die because nobody picked up. Stating a requirement the
  // carrier never answered against — "REQUIRED ≤ —" over "cannot meet the
  // pickup cutoff" — asserts a comparison that never happened, on the loudest
  // screen in the product.
  const unreachable = !constraint || required === "—";
  const reason = unreachable
    ? ((payload.reason as string) ?? replan?.reason ?? event.title)
    : target && rule
      ? `${target} cannot meet ${rule}.`
      : ((payload.reason as string) ?? replan?.reason ?? event.title);

  const callRef =
    view?.operations
      .flatMap((op) => op.calls)
      .find((call) => call.call_id === (replan?.trigger_call_id ?? event.call_id))?.display_ref ??
    "CALL-E";

  return (
    <div
      role="alert"
      aria-live="assertive"
      data-testid="invalidation-overlay"
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/92 px-6 backdrop-blur-sm"
    >
      <div className="w-full max-w-2xl animate-breach-in border border-breach/60 bg-panel">
        <div className="h-1 w-full bg-breach animate-breach-bar" />

        <div className="px-8 py-9">
          <p className="flex items-center gap-3 text-2xl font-700 tracking-tight text-breach">
            <TriangleAlert className="h-6 w-6" />
            RECOVERY PLAN INVALIDATED
          </p>

          <p className="mt-3 max-w-[52ch] text-[17px] leading-relaxed text-chalk">{reason}</p>

          {unreachable ? (
            <div className="mt-8 border border-breach/30 bg-panel px-5 py-4">
              <p className="label">{target}</p>
              <p className="reading mt-1 text-4xl font-500 text-breach">no answer</p>
            </div>
          ) : (
            <div className="mt-8 grid grid-cols-2 gap-px bg-breach/30">
              <div className="bg-panel px-5 py-4">
                <p className="label">Required</p>
                <p className="reading mt-1 text-4xl font-500">
                  {operatorSymbol(operator)} {required}
                </p>
              </div>
              <div className="bg-panel px-5 py-4">
                <p className="label">{target} confirmed</p>
                <p className="reading mt-1 text-4xl font-500 text-breach">{observed}</p>
              </div>
            </div>
          )}

          <div className="mt-7 flex items-end justify-between border-t border-line pt-4">
            <div>
              <p className="label">Evidence</p>
              <p className="reading mt-1 text-sm text-signal">
                {callRef} — {target}
              </p>
            </div>
            <p className="reading text-sm tracking-[0.14em] text-caution">PHONEOPS IS REPLANNING</p>
          </div>
        </div>
      </div>
    </div>
  );
}
