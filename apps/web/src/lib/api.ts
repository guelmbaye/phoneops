import type { HealthOut, MissionSummary, RecoveryView } from "./types";

/** Same-origin: next.config.ts rewrites /api/* to the FastAPI service. */
const BASE = "/api";

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      // The proxy names what it could not reach; keep that, it is the whole
      // diagnosis.
      detail = body?.message ?? body?.detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, res.status);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  health: () => request<HealthOut>("/health"),

  listMissions: () => request<MissionSummary[]>("/recovery-missions"),

  getMission: (id: string) => request<RecoveryView>(`/recovery-missions/${id}`),

  startMission: (id: string) =>
    request<RecoveryView>(`/recovery-missions/${id}/start`, {
      method: "POST",
      body: JSON.stringify({ force: false }),
    }),

  /** Creates (and optionally resets) the flagship Shipment #4821 scenario. */
  createFlagship: (opts: { reset?: boolean; autostart?: boolean } = {}) => {
    const q = new URLSearchParams({
      reset: String(opts.reset ?? false),
      autostart: String(opts.autostart ?? false),
    });
    return request<MissionSummary>(`/demo/flagship?${q}`, { method: "POST" });
  },

  decideApproval: (missionId: string, approvalId: string, approved: boolean) =>
    request<RecoveryView>(`/recovery-missions/${missionId}/approvals/${approvalId}`, {
      method: "POST",
      body: JSON.stringify({ approved, decided_by: "operator", comment: "" }),
    }),

  explain: (id: string) => request<{ mission_id: string; replans: ExplainEntry[] }>(
    `/recovery-missions/${id}/explain`,
  ),
};

export interface ExplainEntry {
  sequence: number;
  what_happened: string;
  why_it_matters: string;
  what_changed: string;
  what_happens_next: string;
  source: {
    call_id: string | null;
    display_ref: string | null;
    provider_mode: string | null;
    evidence_id: string | null;
    observed: string | null;
    required: string | null;
    confidence: string | null;
  };
}

export { ApiError };
