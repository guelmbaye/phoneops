import { factLabel, humanKey, operatorSymbol, readValue } from "@/lib/format";
import type { RecoveryView } from "@/lib/types";
import { Panel } from "./primitives";

/**
 * Screen 02 (DOCUMENT 04 §7): what happened, what we are protecting, what the
 * rules are, and what is still unknown — before any call is placed.
 */
export function RecoveryBrief({
  view,
  showNeeds = true,
}: {
  view: RecoveryView;
  /** Off when the dedicated panel is on screen — otherwise the same questions
   *  appear twice, in two different wordings, on the one beat that has to be
   *  unmistakable. */
  showNeeds?: boolean;
}) {
  // Scoped to the path we are actually on: an abandoned carrier's unanswered
  // questions are not outstanding work, and listing them under a RECOVERED
  // banner contradicts the outcome directly above.
  const subject = view.current_strategy?.target;
  // Once the mission has an outcome nothing is outstanding, whatever the
  // per-candidate records still say. Listing questions under an ESCALATED
  // banner reads as if the system were still working.
  const unresolved = view.outcome
    ? []
    : view.information_needs.filter(
        (n) => n.status !== "resolved" && (!subject || n.subject === subject),
      );

  return (
    <Panel title="Recovery brief">
      <div className="space-y-5">
        <div>
          <p className="label">What happened</p>
          <p className="mt-1 max-w-[62ch] text-[15px] leading-relaxed">
            {view.exception.description}
          </p>
        </div>

        <div>
          <p className="label">Objective</p>
          <p className="mt-1 max-w-[62ch] text-[15px] leading-relaxed text-chalk">
            {view.mission.objective}
          </p>
        </div>

        <div className="grid gap-5 sm:grid-cols-2">
          <div>
            <p className="label">Constraints</p>
            <ul className="mt-1.5 space-y-1">
              {view.constraints.map((c) => (
                <li key={c.id} className="text-sm">
                  <span className="text-chalk">{c.label || humanKey(c.key)}</span>{" "}
                  <span className="reading text-mist">
                    {operatorSymbol(c.operator)} {readValue(c.required_value)}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          {showNeeds && (
          <div>
            <p className="label">Missing information</p>
            <ul className="mt-1.5 space-y-1">
              {unresolved.length === 0 ? (
                <li className="text-sm text-dust">None outstanding.</li>
              ) : (
                unresolved.map((need) => (
                  <li key={need.id} className="text-sm text-chalk">
                    {need.subject} — {factLabel(view.fact_labels, need.key)}
                  </li>
                ))
              )}
            </ul>
          </div>
          )}
        </div>

        <p className="border-t border-line pt-4 text-sm text-mist">
          {/* "Every fact was confirmed by phone" on a mission that discovered
              nothing contradicts the metrics panel on the same screen. */}
          {unresolved.length > 0
            ? "PHONEOPS needs external confirmation before this plan can be validated."
            : view.evidence.length > 0
              ? "Every fact above was confirmed by phone, not assumed."
              : view.operations.length > 0
                ? "No carrier could be reached, so nothing was confirmed."
                : "No connected system holds these answers."}
        </p>
      </div>
    </Panel>
  );
}
