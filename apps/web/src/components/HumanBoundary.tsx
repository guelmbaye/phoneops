"use client";

import { useState } from "react";
import { ShieldAlert } from "lucide-react";

import { api } from "@/lib/api";
import { factLabel, humanKey, readFact } from "@/lib/format";
import type { ApprovalOut, InformationNeedOut } from "@/lib/types";
import { Panel } from "./primitives";

/**
 * Autonomy is not authority (DOCUMENT 09). PHONEOPS calls, verifies and replans
 * on its own; a material cost increase stops at a human.
 */
export function ApprovalPanel({
  approval,
  missionId,
  onDecided,
  labels = {},
}: {
  approval: ApprovalOut;
  missionId: string;
  onDecided: () => void;
  labels?: Record<string, string>;
}) {
  const [busy, setBusy] = useState(false);

  const decide = async (approved: boolean) => {
    setBusy(true);
    try {
      await api.decideApproval(missionId, approval.id, approved);
      onDecided();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title="Approval required"
      tone="caution"
      aside={<ShieldAlert className="h-4 w-4 text-caution" />}
    >
      <p className="max-w-[65ch] text-[15px] leading-relaxed text-chalk">{approval.reason}</p>

      <dl className="mt-4 flex flex-wrap gap-x-10 gap-y-3">
        <div>
          <dt className="label">Action</dt>
          <dd className="mt-0.5 text-sm">{humanKey(approval.action)}</dd>
        </div>
        {Object.entries(approval.payload)
          // Only scalars: an object here rendered as a JSON blob across the
          // panel a human is meant to read before committing money.
          .filter(([, v]) => v !== null && v !== "" && typeof v !== "object")
          .map(([key, value]) => (
            <div key={key}>
              <dt className="label">{factLabel(labels, key)}</dt>
              <dd className="reading mt-0.5 text-sm">
                {readFact(key, { value } as Record<string, unknown>)}
              </dd>
            </div>
          ))}
      </dl>

      <div className="mt-5 flex gap-3">
        <button
          type="button"
          disabled={busy}
          onClick={() => decide(true)}
          className="border border-secure/60 bg-secure/10 px-4 py-2 text-sm font-500 text-secure transition-colors hover:bg-secure/20 disabled:opacity-50"
        >
          Approve the recovery path
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => decide(false)}
          className="border border-line px-4 py-2 text-sm text-mist transition-colors hover:border-breach/60 hover:text-breach disabled:opacity-50"
        >
          Reject
        </button>
      </div>
    </Panel>
  );
}

/**
 * Sponsor necessity, made visible (DOCUMENT 04 §44): the recovery is blocked
 * until someone outside the software answers the phone.
 */
export function MissingInformation({ needs }: { needs: InformationNeedOut[] }) {
  const open = needs.filter((need) => need.status !== "resolved");
  if (open.length === 0) return null;

  return (
    <Panel title="Recovery blocked by missing information" tone="signal">
      <ul className="space-y-2">
        {open.map((need) => (
          <li key={need.id} className="flex items-baseline justify-between gap-4">
            <span className="text-sm text-chalk">{need.description}</span>
            <span className="reading shrink-0 text-[11px] tracking-[0.12em] text-dust">
              {need.status === "requested" ? "ASKING" : "UNKNOWN"}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-4 border-t border-line pt-3 text-sm text-mist">
        This information does not exist in any connected system. PHONEOPS cannot validate the
        recovery plan until CALL-E obtains it.
      </p>
    </Panel>
  );
}
