import clsx from "clsx";
import { PhoneCall } from "lucide-react";

import { factLabel } from "@/lib/format";
import type { OperationOut } from "@/lib/types";
import { Panel, StatusChip, type Tone } from "./primitives";

const OP_TONE: Record<string, Tone> = {
  queued: "neutral",
  calling: "signal",
  completed: "secure",
  failed: "breach",
  cancelled: "neutral",
};

const OP_LABEL: Record<string, string> = {
  queued: "STARTING",
  calling: "IN PROGRESS",
  completed: "COMPLETE",
  failed: "FAILED",
  cancelled: "CANCELLED",
};

/**
 * CALL-E stays visible where it matters (DOCUMENT 04 §43) without turning the
 * product into a call dashboard: no waveform, no live transcript.
 */
export function CallOperationCard({
  operation,
  causedBy,
  discovered,
  labels = {},
}: {
  operation: OperationOut;
  /** Human sentence explaining which earlier evidence produced this call. */
  causedBy?: string | null;
  /** Fact keys this call actually returned. Empty until it lands. */
  discovered?: string[];
  labels?: Record<string, string>;
}) {
  const call = operation.calls.at(-1);
  const tone = OP_TONE[operation.status] ?? "neutral";
  const simulated = call?.provider_mode === "mock";
  const done = operation.status === "completed";
  const over = done || operation.status === "failed" || operation.status === "cancelled";

  return (
    <Panel
      title="Phone operation"
      tone={tone}
      aside={
        <StatusChip tone={tone} live={operation.status === "calling"}>
          {OP_LABEL[operation.status] ?? operation.status}
        </StatusChip>
      }
    >
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <PhoneCall className={clsx("h-4 w-4", tone === "breach" ? "text-breach" : "text-signal")} />
          <span className="reading text-sm font-500 text-signal">CALL-E</span>
          <span className="text-dust">→</span>
          <span className="text-lg font-600">{operation.target}</span>
          {call && <span className="reading ml-auto text-xs text-dust">{call.display_ref}</span>}
        </div>

        {operation.status === "calling" && (
          <div className="h-px w-full overflow-hidden bg-line">
            <div className="h-px w-1/3 bg-signal animate-sweep" />
          </div>
        )}

        <div>
          {/* Past tense once the call has landed: "Discovering" over a
              COMPLETE operation reads as if the call were still running. */}
          {/* Before the call lands, list what CALL-E was asked for. After it
              lands, list what actually came back: a fact that was asked for and
              never answered must not appear under "Discovered". */}
          <p className="label">
            {done ? "Discovered" : over ? "Asked, never answered" : "Discovering"}
          </p>
          <ul className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1">
            {(done ? (discovered ?? []) : operation.requested_facts).map((fact) => (
              <li key={fact} className="text-sm text-chalk">
                {factLabel(labels, fact)}
              </li>
            ))}
          </ul>
          {done &&
            operation.requested_facts
              .filter((fact) => !(discovered ?? []).includes(fact))
              .map((fact) => (
                <p key={fact} className="mt-1.5 text-xs text-dust">
                  {factLabel(labels, fact)} — asked, not answered
                </p>
              ))}
        </div>

        {causedBy && (
          <p className="border-l-2 border-signal bg-signal/5 py-2 pl-3 text-sm text-mist">
            <span className="label mr-2">Triggered by</span>
            {causedBy}
          </p>
        )}

        {operation.attempt_number > 1 && (
          <p className="reading text-xs text-caution">
            Attempt {operation.attempt_number}
            {over ? " — the line was never answered" : " — retrying after an unanswered call"}
          </p>
        )}

        {simulated && (
          <p className="reading text-[11px] tracking-wide text-dust">
            Simulated CALL-E provider — every run is labelled at the source
          </p>
        )}
      </div>
    </Panel>
  );
}
