import clsx from "clsx";
import { ArrowRight } from "lucide-react";

import { operatorSymbol, readValue } from "@/lib/format";
import type { ConstraintOut, RecoveryView, StrategyDiffOut } from "@/lib/types";
import { Panel, StatusChip } from "./primitives";

/**
 * Screen 04 (DOCUMENT 04 §19-20): do not simply swap Carrier B for Carrier C.
 * Show both, and show what made the swap necessary.
 */
export function StrategyDiff({
  diff,
  view,
}: {
  diff: StrategyDiffOut;
  view: RecoveryView;
}) {
  const cutoff: ConstraintOut | undefined = view.constraints.find((c) => c.mandatory);
  const callRef =
    view.operations
      .flatMap((op) => op.calls)
      .find((call) => call.call_id === diff.trigger_call_id)?.display_ref ?? null;

  const required = readValue(diff.required_value);
  const observed = readValue(diff.observed_value);
  // A carrier who never answered has not missed the cutoff. Asserting a
  // constraint failure here contradicts the rule the product rests on: an
  // unanswered call is an operational failure, never business evidence.
  const violated = view.latest_replan?.trigger === "constraint_violated";
  // A carrier that answered, satisfied every constraint and was declined on
  // cost is neither in breach nor unreachable. Without this it was shown as
  // "no answer / Could not be reached" directly above a WHY line saying the
  // operator rejected it.
  const declined = view.latest_replan?.trigger === "assumption_invalidated";
  const rule = cutoff?.short_label ?? "";
  const settled = diff.after?.status === "successful";
  // A replacement can fail too. Saying "VALIDATING" under a card that reads
  // INVALID puts two answers on one screen.
  const failed = diff.after?.status === "invalidated" || diff.after?.status === "abandoned";

  // Once the replacement is confirmed, show what it actually answered rather
  // than the requirement it was asked to meet.
  // Verified but not yet committed: the facts are in and the constraints pass,
  // and what is pending is a human decision, not more verification. Saying
  // "needs verification" asks the operator to approve something the screen
  // claims is unchecked.
  const awaitingApproval = view.mission.status === "approval_required";

  const confirmed = view.constraint_evaluations.find(
    (item) =>
      item.subject === diff.after?.target &&
      item.constraint_key === cutoff?.key &&
      item.status === "satisfied",
  );

  return (
    <Panel title="Recovery strategy changed" tone="caution">
      <div className="grid gap-4 md:grid-cols-[1fr_auto_1fr] md:items-stretch">
        <div className="border border-breach/40 bg-breach-dim/30 p-4">
          <p className="label">Before</p>
          <p className="mt-1 text-xl font-600 text-dust line-through decoration-breach/70">
            {diff.before?.target ?? "—"}
          </p>
          <p className="reading mt-3 text-2xl text-breach">
            {violated || declined ? observed : "no answer"}
          </p>
          <p className="mt-1 text-sm text-breach">
            {violated
              ? `Misses ${rule || "the requirement"}`
              : declined
                ? "Declined by an operator"
                : "Could not be reached"}
          </p>
          <div className="mt-4">
            <StatusChip tone="breach">INVALID</StatusChip>
          </div>
        </div>

        <div className="flex items-center justify-center md:px-1">
          <ArrowRight className="h-5 w-5 text-caution" />
        </div>

        <div
          className={clsx(
            "border p-4",
            failed ? "border-breach/40 bg-breach-dim/30" : "border-signal/40 bg-signal/5",
          )}
        >
          <p className="label">Now</p>
          <p className="mt-1 text-xl font-600">{diff.after?.target ?? "—"}</p>
          <p className="reading mt-3 text-2xl text-chalk">
            {(settled || awaitingApproval) && confirmed
              ? readValue(confirmed.observed_value)
              : failed
                ? "no answer"
                : // A call failure leaves no observed value; fall back to the
                  // mission's own requirement rather than rendering "≤ —".
                  `${cutoff ? operatorSymbol(cutoff.operator) + " " : ""}${
                    required !== "—" ? required : readValue(cutoff?.required_value)
                  }`}
          </p>
          <p className="mt-1 text-sm text-mist">
            {settled || (awaitingApproval && confirmed)
              ? `Meets ${rule || "the requirement"}`
              : failed
                ? "Could not be reached"
                : "Needs verification"}
          </p>
          <div className="mt-4">
            <StatusChip
              tone={
                settled ? "secure" : failed ? "breach" : awaitingApproval ? "caution" : "signal"
              }
              live={!settled && !failed && !awaitingApproval}
            >
              {settled
                ? "CONFIRMED"
                : failed
                  ? "INVALID"
                  : awaitingApproval
                    ? "AWAITING APPROVAL"
                    : "VALIDATING"}
            </StatusChip>
          </div>
        </div>
      </div>

      <div className="mt-4 border-t border-line pt-4">
        <p className="label">Why</p>
        <p className="mt-1 max-w-[70ch] text-sm leading-relaxed text-chalk">
          {violated && diff.before?.target && rule
            ? `${diff.before.target} cannot meet ${rule}.`
            : diff.reason}
        </p>
        {callRef && (
          <p className="reading mt-2 text-xs text-signal">
            Source {callRef} — {diff.before?.target}
          </p>
        )}
      </div>
    </Panel>
  );
}
