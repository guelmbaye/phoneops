"use client";

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { useMission } from "@/hooks/useMission";
import { duration, readFact } from "@/lib/format";
import type { OperationOut, RecoveryView } from "@/lib/types";

import { CallOperationCard } from "./CallOperationCard";
import { ConstraintPanel } from "./ConstraintPanel";
import { EvidenceFeed } from "./EvidenceFeed";
import { ApprovalPanel, MissingInformation } from "./HumanBoundary";
import { InvalidationOverlay } from "./InvalidationOverlay";
import { MissionHeader } from "./MissionHeader";
import { Panel } from "./primitives";
import { RecoveryBrief } from "./RecoveryBrief";
import { RecoveryOutcome } from "./RecoveryOutcome";
import { StrategyCard, StrategyRow } from "./StrategyCard";
import { StrategyDiff } from "./StrategyDiff";
import { Timeline } from "./Timeline";

/**
 * Screen 03 — Adaptive Recovery Control (DOCUMENT 04 §9).
 *
 * Visual hierarchy is fixed by §37: outcome, then time, then current strategy,
 * then new evidence, then constraints, then CALL-E activity.
 */
export function MissionControl({
  missionId,
  autostart,
}: {
  missionId: string;
  autostart: boolean;
}) {
  const { view, error, connection, invalidation, refresh } = useMission(missionId);
  const launched = useRef(false);
  const [launching, setLaunching] = useState(false);

  useEffect(() => {
    if (!autostart || launched.current || !view) return;
    if (view.mission.status !== "assessing") return;
    launched.current = true;
    // Fire and forget: the stream, not this promise, drives the screen.
    void api.startMission(missionId).catch(() => undefined);
  }, [autostart, view, missionId]);

  if (error && !view) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-24">
        <p className="border-l-2 border-breach pl-4 text-breach">{error}</p>
      </main>
    );
  }

  if (!view) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-24">
        <p className="text-dust">Loading recovery mission…</p>
      </main>
    );
  }

  // Pre-flight. The brief and the unanswered questions are the sponsor
  // necessity proof (DOCUMENT 05 §16): before any call, the recovery is
  // demonstrably blocked on information no connected system holds. Autostart
  // flashes past it in a fraction of a second, so the mission page has to hold
  // the state and let an operator launch when ready.
  const preflight = view.mission.status === "assessing" && view.operations.length === 0;

  const operation = view.active_operation ?? view.operations.at(-1) ?? null;
  const subject = operation?.target ?? view.mission.current_strategy_target ?? null;

  return (
    <>
      <MissionHeader
        mission={view.mission}
        risk={view.impact.consequence}
        connection={connection}
      />

      <main className="mx-auto max-w-[1400px] space-y-4 px-5 py-5">
        {view.outcome && <RecoveryOutcome outcome={view.outcome} view={view} />}

        {view.pending_approval && (
          <ApprovalPanel
            approval={view.pending_approval}
            missionId={missionId}
            onDecided={refresh}
            labels={view.fact_labels}
          />
        )}

        {preflight && (
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
            <RecoveryBrief view={view} showNeeds={false} />
            <div className="space-y-4">
              <MissingInformation needs={view.information_needs} />
              <button
                type="button"
                disabled={launching}
                onClick={() => {
                  setLaunching(true);
                  void api.startMission(missionId).catch(() => setLaunching(false));
                }}
                className="w-full border border-signal bg-signal px-6 py-4 text-base font-600 tracking-wide text-white transition-colors hover:bg-signal-dim disabled:opacity-60"
              >
                {launching ? "Calling…" : "Start recovery"}
              </button>
            </div>
          </div>
        )}

        {view.strategy_diff && <StrategyDiff diff={view.strategy_diff} view={view} />}

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(300px,0.85fr)]">
          <div className="space-y-4">
            {view.current_strategy && <StrategyCard
                strategy={view.current_strategy}
                labels={view.fact_labels}
                started={view.operations.length > 0}
                awaitingApproval={view.mission.status === "approval_required"}
                unresolved={view.outcome !== null}
              />}
            {view.strategies.length > 1 && (
              <Panel title="Strategy history">
                <ul>
                  {view.strategies.map((strategy) => (
                    <StrategyRow
                      key={strategy.id}
                      strategy={strategy}
                      awaitingApproval={view.mission.status === "approval_required"}
                      unresolved={view.outcome !== null}
                    />
                  ))}
                </ul>
              </Panel>
            )}
            {/* Stays for the whole mission. On the outcome screen a judge must
                still be able to answer "what broke?" without scrolling back. */}
            {!preflight && <RecoveryBrief view={view} />}
          </div>

          <div className="space-y-4">
            {operation ? (
              <CallOperationCard
                operation={operation}
                causedBy={causeSentence(view, operation)}
                discovered={view.evidence
                  .filter((item) => item.operation_id === operation.id)
                  .map((item) => item.type)}
                labels={view.fact_labels}
              />
            ) : preflight ? null : (
              <MissingInformation needs={view.information_needs} />
            )}
            <ConstraintPanel
              constraints={view.constraints}
              evaluations={view.constraint_evaluations}
              subject={subject}
            />
          </div>

          <EvidenceFeed
            evidence={view.evidence}
            operations={view.operations}
            labels={view.fact_labels}
            finished={view.outcome !== null}
          />
        </div>

        <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <Timeline
            events={view.timeline.filter((e) => e.strategic)}
            timeZone={view.mission.deadline.timezone}
          />
          <MissionMetricsPanel view={view} />
        </div>
      </main>

      {invalidation && <InvalidationOverlay event={invalidation} view={view} />}
    </>
  );
}

/**
 * The causal link UI (DOCUMENT 04 §23) — answers "why are you calling Carrier C?"
 * straight from stored provenance, never from a narrative the UI invented.
 */
function causeSentence(view: RecoveryView, operation: OperationOut): string | null {
  if (!operation.triggered_by_evidence_id) return null;
  const evidence = view.evidence.find((e) => e.id === operation.triggered_by_evidence_id);
  if (!evidence) return null;
  // Same shape as the timeline entry it refers to: "Carrier B pickup time: 18:00".
  const label = (view.fact_labels[evidence.type] ?? evidence.type.replace(/_/g, " ")).toLowerCase();
  return `${evidence.subject} ${label}: ${readFact(evidence.type, evidence.value)}`;
}

function MissionMetricsPanel({ view }: { view: RecoveryView }) {
  const m = view.metrics;
  const rows: [string, string][] = [
    ["Phone operations", String(m.phone_operations)],
    ["Strategies evaluated", String(m.strategies_evaluated)],
    ["Automatic replans", String(m.automatic_replans)],
    ["Human interventions", String(m.human_interventions)],
    ["Facts discovered", String(m.evidence_count)],
    [
      // A mission that escalated did not recover; naming the duration after an
      // outcome it never reached misreports it.
      view.outcome && view.outcome.status !== "recovered"
        ? "Time to escalation"
        : "Time to recovery",
      duration(m.time_to_recovery_seconds),
    ],
  ];

  return (
    <Panel title="Mission metrics">
      <dl className="space-y-2">
        {rows.map(([label, value]) => (
          <div
            key={label}
            className="flex items-baseline justify-between border-b border-line-soft py-1.5 last:border-0"
          >
            <dt className="text-sm text-mist">{label}</dt>
            <dd className="reading text-sm">{value}</dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}
