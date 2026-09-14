import clsx from "clsx";

import { CONFIDENCE_LABEL, EVIDENCE_LABEL, factLabel, readFact } from "@/lib/format";
import type { EvidenceOut, OperationOut } from "@/lib/types";
import { Empty, Panel, type Tone } from "./primitives";

const STATUS_TONE: Record<string, Tone> = {
  validated: "secure",
  discovered: "signal",
  uncertain: "caution",
  conflicting: "caution",
  stale: "neutral",
  rejected: "breach",
};

/**
 * Evidence cards (DOCUMENT 04 §15): a fact, who said it, which CALL-E run
 * produced it, and how much the engine trusts it. No transcript required.
 */
export function EvidenceFeed({
  evidence,
  operations,
  labels,
  finished = false,
}: {
  evidence: EvidenceOut[];
  operations: OperationOut[];
  labels: Record<string, string>;
  /** The mission has an outcome — "yet" no longer applies. */
  finished?: boolean;
}) {
  const refFor = new Map<string, string>();
  for (const op of operations) {
    for (const call of op.calls) refFor.set(call.call_id, call.display_ref);
  }

  return (
    <Panel title="Discovered facts">
      {evidence.length === 0 ? (
        <Empty>
          {finished
            ? "No external facts were obtained. No carrier answered."
            : "No external facts yet. The recovery plan is unverified until CALL-E answers."}
        </Empty>
      ) : (
        <ul className="space-y-2.5">
          {[...evidence].reverse().map((item) => (
            <EvidenceCard
              key={item.id}
              item={item}
              callRef={item.call_id ? refFor.get(item.call_id) : undefined}
              labels={labels}
            />
          ))}
        </ul>
      )}
    </Panel>
  );
}

export function EvidenceCard({
  item,
  callRef,
  labels = {},
}: {
  item: EvidenceOut;
  callRef?: string;
  labels?: Record<string, string>;
}) {
  const tone = STATUS_TONE[item.status] ?? "neutral";
  return (
    <li
      className={clsx(
        "border-l-2 bg-raised/60 px-3 py-2.5",
        tone === "secure" && "border-secure",
        tone === "signal" && "border-signal",
        tone === "caution" && "border-caution",
        tone === "breach" && "border-breach",
        tone === "neutral" && "border-line",
      )}
    >
      <div className="flex items-baseline justify-between gap-3">
        <span className="reading text-xl font-500">{readFact(item.type, item.value)}</span>
        <span
          className={clsx(
            "reading text-[10px] tracking-[0.12em]",
            tone === "secure" && "text-secure",
            tone === "signal" && "text-signal",
            tone === "caution" && "text-caution",
            tone === "breach" && "text-breach",
            tone === "neutral" && "text-dust",
          )}
        >
          {EVIDENCE_LABEL[item.status]}
        </span>
      </div>
      <p className="mt-0.5 text-sm text-chalk">{factLabel(labels, item.type)}</p>
      <p className="mt-1.5 flex flex-wrap items-center gap-x-3 text-xs text-dust">
        <span>{item.subject}</span>
        {callRef && <span className="reading text-signal">{callRef}</span>}
        <span>Confidence {CONFIDENCE_LABEL[item.confidence]}</span>
      </p>
      {item.status === "uncertain" && item.raw_value && (
        <p className="mt-1.5 text-xs text-caution">Heard: “{item.raw_value}” — asking again</p>
      )}
    </li>
  );
}
