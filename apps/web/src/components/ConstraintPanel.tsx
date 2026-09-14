import clsx from "clsx";

import { humanKey, operatorSymbol, readValue } from "@/lib/format";
import type { ConstraintEvaluationOut, ConstraintOut } from "@/lib/types";
import { Empty, Panel } from "./primitives";

/**
 * The causal bridge between CALL-E and replanning (DOCUMENT 04 §16): required
 * versus observed, side by side, with the verdict stated.
 */
export function ConstraintPanel({
  constraints,
  evaluations,
  subject,
}: {
  constraints: ConstraintOut[];
  evaluations: ConstraintEvaluationOut[];
  subject: string | null;
}) {
  // Evidence is scoped per subject, so a Carrier B answer can never appear to
  // validate a Carrier C strategy.
  const scoped = subject ? evaluations.filter((e) => e.subject === subject) : evaluations;
  const latest = new Map<string, ConstraintEvaluationOut>();
  for (const evaluation of scoped) latest.set(evaluation.constraint_key, evaluation);

  return (
    <Panel title={subject ? `Constraints — ${subject}` : "Constraints"}>
      {constraints.length === 0 ? (
        <Empty>No constraints recorded for this mission.</Empty>
      ) : (
        <ul className="space-y-2">
          {constraints.map((constraint) => {
            const evaluation = latest.get(constraint.key);
            const status = evaluation?.status ?? "not_evaluated";
            return (
              <li
                key={constraint.id}
                className="grid grid-cols-[1fr_auto_auto] items-center gap-3 border-b border-line-soft py-2 last:border-0"
              >
                <span className="text-sm text-chalk">
                  {constraint.label || humanKey(constraint.key)}
                  {!constraint.mandatory && (
                    <span className="ml-2 text-xs text-dust">advisory</span>
                  )}
                </span>
                <span className="reading text-sm text-mist">
                  {operatorSymbol(constraint.operator)} {readValue(constraint.required_value)}
                </span>
                <Verdict status={status} observed={evaluation?.observed_value} />
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

function Verdict({
  status,
  observed,
}: {
  status: string;
  observed?: Record<string, unknown>;
}) {
  const map: Record<string, [string, string]> = {
    satisfied: ["text-secure", "SATISFIED"],
    violated: ["text-breach", "VIOLATED"],
    uncertain: ["text-caution", "UNCERTAIN"],
    not_evaluated: ["text-dust", "UNKNOWN"],
  };
  const [color, label] = map[status] ?? map.not_evaluated;
  return (
    <span className="flex items-center gap-3 justify-self-end">
      <span className={clsx("reading text-sm", color)}>
        {status === "not_evaluated" ? "—" : readValue(observed)}
      </span>
      <span className={clsx("reading w-[74px] text-right text-[10px] tracking-[0.1em]", color)}>
        {label}
      </span>
    </span>
  );
}

/**
 * The required-vs-observed comparison, blown up. Used inside the invalidation
 * hero moment and the strategy diff.
 */
export function ComparisonBlock({
  requiredLabel,
  required,
  observed,
  observedLabel,
}: {
  requiredLabel: string;
  required: string;
  observed: string;
  observedLabel: string;
}) {
  return (
    <div className="grid grid-cols-2 gap-px bg-line">
      <div className="bg-panel px-5 py-4">
        <p className="label">{requiredLabel}</p>
        <p className="reading mt-1 text-3xl font-500">{required}</p>
      </div>
      <div className="bg-panel px-5 py-4">
        <p className="label">{observedLabel}</p>
        <p className="reading mt-1 text-3xl font-500 text-breach">{observed}</p>
      </div>
    </div>
  );
}
