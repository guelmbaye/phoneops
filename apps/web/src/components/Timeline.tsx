import { Fragment } from "react";

import clsx from "clsx";

import { clock } from "@/lib/format";
import type { EventOut } from "@/lib/types";
import { Panel } from "./primitives";

const TONE: Record<string, string> = {
  CONSTRAINT_VIOLATED: "text-breach",
  STRATEGY_INVALIDATED: "text-breach",
  CALL_FAILED: "text-breach",
  RECOVERY_ESCALATED: "text-breach",
  RECOVERY_REPLANNED: "text-caution",
  APPROVAL_REQUIRED: "text-caution",
  EVIDENCE_DISCOVERED: "text-signal",
  CALL_REQUESTED: "text-signal",
  CALL_COMPLETED: "text-signal",
  RECOVERY_COMPLETED: "text-secure",
  RECOVERY_PATH_CONFIRMED: "text-secure",
};

const DOT: Record<string, string> = {
  CONSTRAINT_VIOLATED: "bg-breach",
  STRATEGY_INVALIDATED: "bg-breach",
  CALL_FAILED: "bg-breach",
  RECOVERY_ESCALATED: "bg-breach",
  RECOVERY_REPLANNED: "bg-caution",
  APPROVAL_REQUIRED: "bg-caution",
  RECOVERY_COMPLETED: "bg-secure",
  RECOVERY_PATH_CONFIRMED: "bg-secure",
};

const dayOf = (iso: string, timeZone: string) =>
  new Date(iso).toLocaleDateString("en-CA", { timeZone });

const dayLabel = (iso: string, timeZone: string) =>
  new Date(iso).toLocaleDateString("en-GB", {
    timeZone,
    weekday: "short",
    day: "numeric",
    month: "short",
  });

/** Strategic events only — the engine filters out its own internal chatter. */
export function Timeline({
  events,
  timeZone,
}: {
  events: EventOut[];
  timeZone: string;
}) {
  return (
    <Panel title="Recovery timeline">
      <ol className="relative space-y-0">
        <span
          className="absolute left-[3px] top-2 bottom-2 w-px bg-line"
          aria-hidden
        />
        {events.map((event, index) => {
          // A night recovery runs 23:58 -> 00:05, and a bare clock makes the
          // audit trail look like time went backwards. Mark the boundary.
          const previous = index > 0 ? events[index - 1] : null;
          const newDay =
            previous !== null &&
            dayOf(previous.created_at, timeZone) !==
              dayOf(event.created_at, timeZone);
          return (
            <Fragment key={event.id}>
              {newDay && (
                <li className="relative py-2 pl-6">
                  <span className="label border-t border-line-soft pt-2 block">
                    {dayLabel(event.created_at, timeZone)}
                  </span>
                </li>
              )}
              {renderEvent(event, timeZone)}
            </Fragment>
          );
        })}
      </ol>
    </Panel>
  );
}

function renderEvent(event: EventOut, timeZone: string) {
  return (
    <li className="relative flex gap-4 py-1.5 pl-6">
      <span
        className={clsx(
          "absolute left-0 top-[11px] h-[7px] w-[7px] rounded-full",
          DOT[event.type] ?? "bg-signal",
        )}
        aria-hidden
      />
      <span className="reading w-12 shrink-0 text-xs text-dust">
        {clock(event.created_at, timeZone)}
      </span>
      <span className={clsx("text-sm", TONE[event.type] ?? "text-chalk")}>
        {event.title}
      </span>
    </li>
  );
}
