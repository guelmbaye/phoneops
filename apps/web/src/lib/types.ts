/**
 * Mirror of `app/domain/schemas.py`.
 *
 * The UI is a projection of backend truth (DOCUMENT 08 §40): it never recomputes
 * whether a plan is still valid, it renders what the engine decided.
 */

export type MissionStatus =
  | "assessing" | "planning" | "executing" | "learning" | "evaluating"
  | "replanning" | "approval_required" | "recovered" | "escalated" | "failed";

export type StrategyStatus = "proposed" | "active" | "invalidated" | "successful" | "abandoned";
export type OperationStatus = "queued" | "calling" | "completed" | "failed" | "cancelled";
export type ConstraintStatus = "satisfied" | "violated" | "uncertain" | "not_evaluated";
export type EvidenceStatus =
  | "discovered" | "validated" | "uncertain" | "conflicting" | "stale" | "rejected";
export type Confidence = "high" | "medium" | "low";
export type Severity = "low" | "medium" | "high" | "critical";
export type OutcomeStatus = "recovered" | "escalated" | "failed";

export interface ExceptionOut {
  id: string;
  type: string;
  entity_ref: string;
  description: string;
  severity: Severity;
  detected_at: string;
  threatened_outcome: string;
  consequence: string;
  source: string;
}

export interface ConstraintOut {
  id: string;
  key: string;
  label: string;
  /** Short noun phrase ("the pickup cutoff"), worded by the backend. */
  short_label: string;
  operator: string;
  value_type: string;
  required_value: Record<string, unknown>;
  mandatory: boolean;
}

export interface CandidateOut {
  id: string;
  name: string;
  phone: string;
  region: string;
  locale: string;
  rank: number;
  eligible: boolean;
  attempted: boolean;
  exhausted: boolean;
}

export interface StrategyOut {
  id: string;
  version: number;
  type: string;
  target: string | null;
  status: StrategyStatus;
  rationale: string;
  information_needed: string[];
  invalidated_reason: string | null;
  invalidated_at: string | null;
  generated_by: string;
  created_at: string;
}

export interface InformationNeedOut {
  id: string;
  key: string;
  subject: string;
  description: string;
  required_for: string;
  status: "unknown" | "requested" | "resolved";
}

export interface CallOut {
  id: string;
  call_id: string;
  display_ref: string;
  provider: string;
  /** "mock" | "http" | "cli" — a simulated run can never be mistaken for a real one. */
  provider_mode: string;
  status: string;
  outcome: string | null;
  summary: string;
  duration_seconds: number | null;
  started_at: string | null;
  ended_at: string | null;
}

export interface OperationOut {
  id: string;
  sequence: number;
  target: string;
  target_phone: string;
  purpose: string;
  status: OperationStatus;
  attempt_number: number;
  requested_facts: string[];
  questions: string[];
  reason: string;
  /** Causal provenance: this call exists because of that evidence. */
  triggered_by_evidence_id: string | null;
  triggered_by_replan_id: string | null;
  strategy_id: string;
  requested_at: string | null;
  completed_at: string | null;
  calls: CallOut[];
}

export interface EvidenceOut {
  id: string;
  subject: string;
  type: string;
  value: { value: unknown } & Record<string, unknown>;
  value_type: string;
  raw_value: string;
  confidence: Confidence;
  status: EvidenceStatus;
  call_id: string | null;
  operation_id: string | null;
  strategy_id: string | null;
  observed_at: string;
  valid_until: string | null;
  extractor: string;
  notes: string;
}

export interface ConstraintEvaluationOut {
  id: string;
  constraint_id: string;
  constraint_key: string;
  subject: string;
  status: ConstraintStatus;
  required_value: Record<string, unknown>;
  observed_value: Record<string, unknown>;
  operator: string;
  mandatory: boolean;
  evidence_ids: string[];
  explanation: string;
  strategy_id: string;
  created_at: string;
}

export interface ReplanOut {
  id: string;
  sequence: number;
  trigger: string;
  previous_strategy_id: string;
  new_strategy_id: string | null;
  trigger_evidence_id: string | null;
  trigger_call_id: string | null;
  violated_constraint_id: string | null;
  reason: string;
  required_value: Record<string, unknown>;
  observed_value: Record<string, unknown>;
  generated_by: string;
  created_at: string;
}

export interface ApprovalOut {
  id: string;
  action: string;
  reason: string;
  payload: Record<string, unknown>;
  status: "pending" | "granted" | "rejected";
  decided_by: string | null;
  decided_at: string | null;
}

export interface OutcomeOut {
  id: string;
  status: OutcomeStatus;
  selected_target: string | null;
  protected_outcome: string;
  headline: string;
  margin_seconds: number | null;
  facts: Record<string, unknown>;
  evidence_ids: string[];
  metrics: Record<string, unknown>;
  completed_at: string;
}

export interface EventOut {
  id: string;
  sequence: number;
  type: string;
  actor: string;
  title: string;
  strategic: boolean;
  strategy_id: string | null;
  operation_id: string | null;
  call_id: string | null;
  evidence_id: string | null;
  constraint_id: string | null;
  replan_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface DeadlineOut {
  cutoff_at: string;
  remaining_seconds: number;
  expired: boolean;
  /** IANA zone the mission's wall-clock times belong to. */
  timezone: string;
}

export interface MissionSummary {
  id: string;
  status: MissionStatus;
  objective: string;
  severity: Severity;
  entity_ref: string;
  exception_type: string;
  deadline: DeadlineOut;
  current_strategy_target: string | null;
  replan_count: number;
  operation_count: number;
  created_at: string;
}

export interface StrategyDiffOut {
  before: StrategyOut | null;
  after: StrategyOut | null;
  reason: string;
  required_value: Record<string, unknown>;
  observed_value: Record<string, unknown>;
  trigger_call_id: string | null;
  trigger_evidence_id: string | null;
}

export interface MissionMetrics {
  phone_operations: number;
  strategies_evaluated: number;
  automatic_replans: number;
  human_interventions: number;
  evidence_count: number;
  /** Detection → confirmed recovery: what the operation was exposed to. */
  time_to_recovery_seconds: number | null;
  /** Start → completion. Engine execution only; not shown on the dashboard. */
  engine_seconds?: number | null;
}

export interface RecoveryView {
  mission: MissionSummary;
  exception: ExceptionOut;
  impact: { threatened_outcome: string; consequence: string; severity: string } & Record<string, unknown>;
  constraints: ConstraintOut[];
  information_needs: InformationNeedOut[];
  current_strategy: StrategyOut | null;
  strategies: StrategyOut[];
  active_operation: OperationOut | null;
  operations: OperationOut[];
  evidence: EvidenceOut[];
  constraint_evaluations: ConstraintEvaluationOut[];
  latest_replan: ReplanOut | null;
  strategy_diff: StrategyDiffOut | null;
  pending_approval: ApprovalOut | null;
  outcome: OutcomeOut | null;
  /** Canonical wording per fact key, worded by the backend. */
  fact_labels: Record<string, string>;
  timeline: EventOut[];
  metrics: MissionMetrics;
  candidates: CandidateOut[];
}

export interface HealthOut {
  status: "ok" | "degraded";
  app: string;
  version: string;
  env: string;
  calle_mode: string;
  calle_configured: boolean;
  llm_enabled: boolean;
  database: string;
  event_bus: string;
}

export const TERMINAL_STATUSES: ReadonlySet<MissionStatus> = new Set([
  "recovered",
  "escalated",
  "failed",
]);

/**
 * The stream sends named SSE events (`event: CALL_REQUESTED`), so `onmessage`
 * never fires — a listener must be registered per type.
 */
export const EVENT_TYPES = [
  "EXCEPTION_DETECTED", "RECOVERY_MISSION_CREATED", "RECOVERY_STARTED",
  "RECOVERY_PLAN_CREATED", "INFORMATION_GAP_IDENTIFIED", "RECOVERY_ATTEMPT_STARTED",
  "CALL_REQUESTED", "CALL_STARTED", "CALL_COMPLETED", "CALL_FAILED",
  "EVIDENCE_DISCOVERED", "EVIDENCE_UNCERTAIN", "EVIDENCE_CONFLICTING",
  "CONSTRAINT_EVALUATED", "CONSTRAINT_VIOLATED", "STRATEGY_INVALIDATED",
  "REPLAN_STARTED", "RECOVERY_REPLANNED", "CLARIFICATION_REQUIRED",
  "RECOVERY_ATTEMPT_COMPLETED", "RECOVERY_PATH_CONFIRMED", "APPROVAL_REQUIRED",
  "APPROVAL_GRANTED", "APPROVAL_REJECTED", "POLICY_BLOCKED",
  "RECOVERY_COMPLETED", "RECOVERY_ESCALATED", "MISSION_HEARTBEAT",
] as const;

export type EventType = (typeof EVENT_TYPES)[number];

export interface StreamEvent {
  id: string;
  sequence: number;
  type: EventType | string;
  actor: string;
  title: string;
  strategic: boolean;
  mission_id: string;
  strategy_id: string | null;
  operation_id: string | null;
  call_id: string | null;
  evidence_id: string | null;
  constraint_id: string | null;
  replan_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}
