import clsx from "clsx";
import { Check, CircleDot, X } from "lucide-react";

import { factLabel, humanKey } from "@/lib/format";
import type { StrategyOut } from "@/lib/types";
import { Panel, StatusChip, type Tone } from "./primitives";

const STATUS_TONE: Record<string, Tone> = {
  proposed: "neutral",
  active: "signal",
  invalidated: "breach",
  successful: "secure",
  abandoned: "neutral",
};

const STATUS_LABEL: Record<string, string> = {
  proposed: "PROPOSED",
  active: "VALIDATING",
  invalidated: "INVALID",
  successful: "CONFIRMED",
  abandoned: "ABANDONED",
};

/**
 * The recovery strategy is a first-class object (DOCUMENT 04 §8), not something
 * hidden inside a chat log.
 */
export function StrategyCard({
  strategy,
  labels = {},
  started = true,
  awaitingApproval = false,
  unresolved = false,
}: {
  strategy: StrategyOut;
  labels?: Record<string, string>;
  /** False until a call is out. "VALIDATING" implies a call in flight; before
   *  one exists the plan is merely proposed. */
  started?: boolean;
  /** Verified by phone, held for a human decision — not still being checked. */
  awaitingApproval?: boolean;
  /** The mission ended without this strategy ever being judged — because the
   *  call never happened, not because the carrier failed. */
  unresolved?: boolean;
}) {
  const proposed = !started && strategy.status === "active";
  const held = awaitingApproval && strategy.status === "active";
  const notEvaluated = unresolved && strategy.status === "active";
  const tone = proposed || notEvaluated
    ? "neutral"
    : held
      ? "caution"
      : (STATUS_TONE[strategy.status] ?? "neutral");
  return (
    <Panel
      title={`Recovery strategy ${String(strategy.version).padStart(2, "0")}`}
      tone={tone}
      aside={
        <StatusChip tone={tone}>
          {notEvaluated
            ? "NOT EVALUATED"
            : proposed
              ? "PROPOSED"
              : held
                ? "AWAITING APPROVAL"
                : (STATUS_LABEL[strategy.status] ?? strategy.status)}
        </StatusChip>
      }
    >
      <div className="space-y-4">
        <div>
          <p className="label">{humanKey(strategy.type)}</p>
          <p className="mt-1 text-2xl font-600 tracking-tight">{strategy.target ?? "—"}</p>
        </div>

        <p className="max-w-[60ch] text-sm leading-relaxed text-mist">{strategy.rationale}</p>

        {strategy.information_needed.length > 0 && (
          <div>
            <p className="label">
              {strategy.status === "successful" || held
                ? "Confirmed by phone"
                : notEvaluated
                  ? "Never asked"
                  : strategy.status === "invalidated" || strategy.status === "abandoned"
                    ? "Never established"
                    : "Need to confirm"}
            </p>
            <ul className="mt-2 space-y-1.5">
              {strategy.information_needed.map((need) => (
                <li key={need} className="flex items-center gap-2 text-sm text-chalk">
                  <Icon status={strategy.status} held={held} />
                  {factLabel(labels, need)}
                </li>
              ))}
            </ul>
          </div>
        )}

        {strategy.invalidated_reason && (
          <p className="border-l-2 border-breach pl-3 text-sm text-breach">
            {strategy.invalidated_reason}
          </p>
        )}
      </div>
    </Panel>
  );
}

function Icon({ status, held = false }: { status: string; held?: boolean }) {
  if (status === "successful" || held) return <Check className="h-3.5 w-3.5 text-secure" />;
  if (status === "invalidated") return <X className="h-3.5 w-3.5 text-breach" />;
  return <CircleDot className="h-3.5 w-3.5 text-dust" />;
}

/** Compact row used in the strategy history list. */
export function StrategyRow({
  strategy,
  awaitingApproval = false,
  unresolved = false,
}: {
  strategy: StrategyOut;
  awaitingApproval?: boolean;
  unresolved?: boolean;
}) {
  const held = awaitingApproval && strategy.status === "active";
  const notEvaluated = unresolved && strategy.status === "active";
  const tone = notEvaluated
    ? "neutral"
    : held
      ? "caution"
      : (STATUS_TONE[strategy.status] ?? "neutral");
  return (
    <li className="flex items-center justify-between gap-4 border-b border-line-soft py-2 last:border-0">
      <span className="flex items-center gap-3">
        <span className="reading text-xs text-dust">
          {String(strategy.version).padStart(2, "0")}
        </span>
        <span
          className={clsx(
            "text-sm",
            strategy.status === "invalidated" ? "text-dust line-through" : "text-chalk",
          )}
        >
          {strategy.target ?? "—"}
        </span>
      </span>
      <StatusChip tone={tone}>
        {notEvaluated
          ? "NOT EVALUATED"
          : held
            ? "AWAITING APPROVAL"
            : (STATUS_LABEL[strategy.status] ?? strategy.status)}
      </StatusChip>
    </li>
  );
}
