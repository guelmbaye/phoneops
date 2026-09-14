import clsx from "clsx";
import { CircleAlert, CircleCheck } from "lucide-react";

import { deadline, duration, factLabel, readFact } from "@/lib/format";
import type { OutcomeOut, RecoveryView } from "@/lib/types";

/**
 * Screen 05 (DOCUMENT 04 §46 / DOCUMENT 05 §31): close the business loop.
 * The operation was protected, or PHONEOPS escalated — never a fabricated win.
 */
export function RecoveryOutcome({ outcome, view }: { outcome: OutcomeOut; view: RecoveryView }) {
  const recovered = outcome.status === "recovered";
  // On an escalation `facts` holds a reason and an internal rule name, not
  // findings. Rendering them through the tick row put a green check against
  // "no remaining candidate satisfies the mandatory constraints".
  const facts = recovered
    ? Object.entries(outcome.facts).filter(([, v]) => v !== null && v !== "")
    : [];
  const reason = recovered ? "" : String(outcome.facts.reason ?? "");
  const attempted = view.candidates.filter((c) => c.attempted).map((c) => c.name);

  return (
    <section
      className={clsx(
        "border bg-panel",
        recovered ? "border-secure/50" : "border-breach/50",
      )}
    >
      <div className={clsx("h-1 w-full", recovered ? "bg-secure" : "bg-breach")} />
      <div className="px-6 py-7">
        <p
          className={clsx(
            "flex items-center gap-3 text-2xl font-700 tracking-tight",
            recovered ? "text-secure" : "text-breach",
          )}
        >
          {recovered ? <CircleCheck className="h-6 w-6" /> : <CircleAlert className="h-6 w-6" />}
          {outcome.headline}
        </p>

        <div className="mt-6 grid gap-x-10 gap-y-5 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="Shipment" value={view.mission.entity_ref} />
          <Stat
            label={recovered ? "Confirmed with" : "Attempted"}
            value={
              recovered
                ? (outcome.selected_target ?? "—")
                : attempted.length > 0
                  ? attempted.join(", ")
                  : "no candidate reached"
            }
          />
          <Stat
            label="Required cutoff"
            value={deadline(view.mission.deadline.cutoff_at, view.mission.deadline.timezone)}
            mono
          />
          {outcome.margin_seconds !== null && (
            <Stat
              label="Margin"
              value={duration(outcome.margin_seconds)}
              mono
              tone={recovered ? "secure" : "breach"}
            />
          )}
        </div>

        {facts.length > 0 && (
          <ul className="mt-6 flex flex-wrap gap-x-8 gap-y-2 border-t border-line pt-4">
            {facts.map(([key, value]) => (
              <li key={key} className="flex items-baseline gap-2 text-sm">
                <span className={clsx(recovered ? "text-secure" : "text-mist")}>✓</span>
                <span className="text-mist">{factLabel(view.fact_labels, key)}</span>
                <span className="reading text-chalk">
                  {readFact(key, { value } as Record<string, unknown>)}
                </span>
              </li>
            ))}
          </ul>
        )}

        {reason && (
          <p className="mt-6 max-w-[70ch] border-l-2 border-breach pl-3 text-[15px] leading-relaxed text-chalk">
            {reason}
          </p>
        )}

        <p className="mt-6 text-sm text-mist">
          {recovered ? outcome.protected_outcome : `${outcome.protected_outcome} is now with an operator.`}
        </p>
      </div>
    </section>
  );
}

function Stat({
  label,
  value,
  mono,
  tone,
}: {
  label: string;
  value: string;
  mono?: boolean;
  tone?: "secure" | "breach";
}) {
  return (
    <div>
      <p className="label">{label}</p>
      <p
        className={clsx(
          "mt-1 text-xl font-600",
          mono && "reading font-500",
          tone === "secure" && "text-secure",
          tone === "breach" && "text-breach",
        )}
      >
        {value}
      </p>
    </div>
  );
}
