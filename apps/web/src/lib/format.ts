import type { Confidence, EvidenceStatus, MissionStatus, OutcomeStatus } from "./types";

/** Unwraps the backend's `{value: ...}` envelope into something printable. */
export function readValue(box: Record<string, unknown> | null | undefined): string {
  if (box === null || box === undefined) return "—";
  const raw = "value" in box ? box.value : box;
  if (raw === null || raw === undefined) return "—";
  if (typeof raw === "boolean") return raw ? "yes" : "no";
  if (typeof raw === "object") return JSON.stringify(raw);
  return String(raw);
}

const OPERATORS: Record<string, string> = {
  lte: "≤",
  gte: "≥",
  lt: "<",
  gt: ">",
  eq: "=",
  neq: "≠",
  in: "∈",
};

export const operatorSymbol = (op: string): string => OPERATORS[op] ?? op;

/**
 * Display name for a fact, using the mission's own wording.
 *
 * Deriving it locally is how the same fact ended up called "availability" in the
 * timeline and "Available" on the card next to it. The backend words it; this
 * only capitalises for a standalone label.
 */
export function factLabel(labels: Record<string, string>, key: string): string {
  const worded = labels[key];
  if (!worded) return humanKey(key);
  return worded.replace(/^\w/, (c) => c.toUpperCase());
}

export function humanKey(key: string): string {
  // A trailing unit belongs on the value, not in the label: "Cost Increase 0%"
  // reads, "Cost Increase Pct 0" does not.
  return key
    .replace(/_(pct|percent)$/, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Renders a discovered fact with the unit its key implies. */
export function readFact(key: string, box: Record<string, unknown> | null | undefined): string {
  const text = readValue(box);
  if (text === "—") return text;
  return /_(pct|percent)$/.test(key) ? `${text}%` : text;
}

/**
 * 24-hour, and always in the mission's operating timezone.
 *
 * The browser's own zone is the wrong frame: a 17:30 cutoff rendered one hour
 * east reads 18:30, directly contradicting the "must be before 17:30"
 * constraint on the same screen — and with it the whole argument that 18:00 is
 * a violation.
 */
export function clock(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: timeZone || "UTC",
  });
}

/**
 * A deadline, with its day when that is not today.
 *
 * "17:30" is unambiguous only while the cutoff falls on the current operational
 * day. It does not for a window that crosses midnight, nor for the demo once it
 * rolls forward — and "Cutoff 17:30" above a 23-hour countdown reads as a bug.
 */
export function deadline(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return "—";
  const tz = timeZone || "UTC";
  const time = clock(iso, tz);

  const day = (d: Date) =>
    d.toLocaleDateString("en-CA", { timeZone: tz }); // YYYY-MM-DD, sortable

  const cutoff = new Date(iso);
  const today = day(new Date());
  const target = day(cutoff);
  if (target === today) return time;

  const tomorrow = day(new Date(Date.now() + 86_400_000));
  if (target === tomorrow) return `Tomorrow ${time}`;

  const weekday = cutoff.toLocaleDateString("en-GB", { timeZone: tz, weekday: "short" });
  return `${weekday} ${time}`;
}

export function countdown(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = String(Math.floor(s / 3600)).padStart(2, "0");
  const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const sec = String(s % 60).padStart(2, "0");
  return `${h}:${m}:${sec}`;
}

/**
 * A duration at the precision it actually has.
 *
 * Rounding up to "1 min" understated a 3-second recovery by a factor of twenty —
 * on the one metric that supports the North Star.
 */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  return m ? `${h} h ${m} min` : `${h} h`;
}


/** DOCUMENT 04 §39 — one status vocabulary across API, audit trail and UI. */
export const MISSION_LABEL: Record<MissionStatus, string> = {
  assessing: "ASSESSING",
  planning: "PLANNING",
  executing: "RECOVERING",
  learning: "LEARNING",
  evaluating: "EVALUATING",
  replanning: "REPLANNING",
  approval_required: "APPROVAL REQUIRED",
  recovered: "RECOVERED",
  escalated: "ESCALATED",
  failed: "FAILED",
};

export const EVIDENCE_LABEL: Record<EvidenceStatus, string> = {
  discovered: "DISCOVERED",
  validated: "CONFIRMED",
  uncertain: "NEEDS CONFIRMATION",
  conflicting: "CONFLICTING",
  stale: "STALE",
  rejected: "REJECTED",
};

export const CONFIDENCE_LABEL: Record<Confidence, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
};

export const OUTCOME_LABEL: Record<OutcomeStatus, string> = {
  recovered: "RECOVERED",
  escalated: "ESCALATED",
  failed: "FAILED",
};

