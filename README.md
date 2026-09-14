# PHONEOPS AI

**Autonomous Exception Recovery for Phone-Dependent Operations**

> When the plan breaks, PHONEOPS calls, learns and recovers.

Operational software knows what was *supposed* to happen. When that plan breaks, the
information needed to recover often sits outside connected systems and still has to be
obtained from people by phone. PHONEOPS uses **CALL-E** to reach those people, turns their
answers into structured evidence, evaluates that evidence against hard operational
constraints, and changes its recovery strategy when reality makes the current plan
impossible.

```
EXCEPTION → OBJECTIVE → MISSING INFORMATION → CALL-E → EVIDENCE
   → CONSTRAINT CHECK → PLAN INVALIDATED → REPLAN → CALL-E AGAIN → RECOVERED
```

**The call does not complete the workflow. The call changes the recovery plan.**

---

## The 60-second proof

```bash
cd apps/api && pip install -r requirements-dev.txt
make -C ../.. demo            # run the flagship recovery mission
make -C ../.. counterfactual  # prove the second call is not hard-coded
```

`make demo` output (abridged):

```
  1  EXCEPTION_DETECTED      Carrier Cancellation - Shipment #4821
  4  RECOVERY_PLAN_CREATED   Recovery strategy 01 - Carrier B
  7  CALL_REQUESTED          CALL-E -> Carrier B
 11  EVIDENCE_DISCOVERED     pickup_time = 18:00
 13  CONSTRAINT_VIOLATED     Required pickup_time <= 17:30; observed 18:00 -> VIOLATED.
 16  STRATEGY_INVALIDATED    Recovery plan invalidated - Carrier B
 18  RECOVERY_REPLANNED      New strategy 02 - Carrier C
 20  CALL_REQUESTED          CALL-E -> Carrier C
 24  EVIDENCE_DISCOVERED     pickup_time = 16:45
 30  RECOVERY_COMPLETED      TONIGHT'S SHIPMENT DEPARTURE PROTECTED
```

---

## Flagship scenario

Shipment #4821 must leave tonight. Carrier A cancels the pickup. The warehouse closes at
17:30. Nothing in the TMS can say which carrier is actually able to recover it *right now*
— that answer lives with dispatchers, on the phone.

| Step | What happens | Who decides |
|---|---|---|
| 1 | Exception creates a Recovery Mission with a measurable objective and hard constraints | deterministic |
| 2 | Planner proposes Carrier B and records what is still **unknown** | AI proposes |
| 3 | CALL-E calls Carrier B with a goal and a strict `result_schema` | CALL-E |
| 4 | The answer becomes evidence: `pickup_time = 18:00`, source `CALL-E #001` | extraction |
| 5 | Constraint engine: `18:00 <= 17:30` → **VIOLATED** | deterministic |
| 6 | Strategy 01 invalidated, with the violated constraint and its evidence stored | deterministic |
| 7 | Replanner picks Carrier C **because** of that evidence | AI proposes |
| 8 | CALL-E calls Carrier C → `16:45` → all constraints satisfied | CALL-E |
| 9 | `RECOVERED` — departure protected with a 45-minute margin | deterministic |

---

## Why the demo is not scripted

`make counterfactual` runs the same mission twice, changing only what the carrier says:

```
CASE A  Carrier B answers 16:00     CASE B  Carrier B answers 18:00
  CALL-E #001 -> Carrier B            CALL-E #001 -> Carrier B
  strategy v1  Carrier B  successful  strategy v1  Carrier B  invalidated
  replans: 0                          strategy v2  Carrier C  successful
  Carrier C is NEVER called           CALL-E #002 -> Carrier C   (replans: 1)
```

Both runs recover the shipment. Only one of them ever creates a second phone operation,
and that operation stores the evidence id, the call id and the violated constraint that
caused it. This is pinned by `tests/e2e/test_counterfactual.py`.

---

## Architecture

```
Next.js Mission Control
        │  REST + SSE
        ▼
      FastAPI ──────────────────────────────────────────┐
        │                                               │
   Recovery Director ── Planner ── Call Operations ──► CALL-E ──► external actors
        │                                               │
        │              Intelligence ◄────────────────────┘  (call result)
        │                    │
        │              MISSION STATE  (PostgreSQL — the single source of truth)
        │                    │
        │             Constraint Engine (deterministic)
        │                ┌───┴───┐
        │            satisfied  violated
        │                │        │
        │            Decision   Replanner ──► new strategy ──► CALL-E again
        │                │
        └──────────► Policy / Approval / Escalation
```

**No agent owns the truth — Mission State does.** Six logical roles (Director, Planner,
Call Operations, Intelligence, Replanner, Decision/Policy) live inside one modular
monolith. They are implementation detail, not the pitch.

### The boundary that makes it trustworthy

| Agentic (LLM may propose) | Deterministic (system enforces) |
|---|---|
| Interpret the exception | Deadline and constraint comparison |
| Generate a recovery hypothesis | Strategy invalidation |
| Identify the information gap | State transitions |
| Extract semantic evidence from a call | Schema validation |
| Propose an alternative strategy | Policy, retry and replan limits |
| Explain in natural language | Mission completion |

An LLM cannot declare a hard constraint satisfied. `LLM_ENABLED=false` is the default and
the entire loop still runs — the models add semantics, never authority.

---

## CALL-E integration

CALL-E is not a voice layer bolted on the side; it is the **evidence acquisition layer**.
Remove it and the recovery engine loses the information required to select the next viable
path. Every phone interaction goes through one adapter (`app/integrations/calle/`), which
owns credentials, idempotency, retries, result normalisation and call-id provenance.

Three interchangeable modes:

| `CALLE_MODE` | Transport | Use |
|---|---|---|
| `http` | `POST /v1/calls`, `GET /v1/calls/{id}`, webhook | **submission demo** |
| `cli` | local `calle` CLI / MCP `plan_call`, `run_call`, `get_call_run` | agent-install onboarding |
| `mock` | deterministic scripted personas | tests, CI, offline dev |

PHONEOPS asks CALL-E for exactly the facts the mission is missing, via a strict
`result_schema` with a `*_confirmed` flag per fact — so a hedged answer ("probably around
5:30") can never be silently promoted into a satisfied cutoff.

```jsonc
{
  "task": "You are calling Carrier B on behalf of the shipper's operations team...",
  "recipient": { "phone": "+1...", "region": "US", "locale": "en-US" },
  "result_schema": {
    "type": "object",
    "required": ["available", "pickup_time", "capacity_ok", "cost_increase_pct"],
    "properties": {
      "pickup_time":           { "type": ["string", "null"] },
      "pickup_time_confirmed": { "type": ["boolean", "null"] }
    },
    "additionalProperties": false
  },
  "metadata": { "mission_id": "...", "operation_id": "...", "strategy_id": "..." },
  "webhook_url": "https://.../api/webhooks/calle"
}
```

Results arrive by webhook (`POST /api/webhooks/calle`, HMAC-verified, idempotent) or by
polling when no public URL is available. Every mocked call is labelled `provider_mode:
"mock"` in the database and the API — a mocked run can never be mistaken for a real one.

---

## Trust and safety

- **Provenance** — every mission-changing fact links to `evidence → operation → call_id →
  external actor`. `GET /api/recovery-missions/{id}/explain` always answers *"which CALL-E
  interaction caused this replan?"*.
- **Uncertainty reduces autonomy** — hedged, conflicting or stale evidence triggers a
  clarification call, never a decision.
- **A no-answer is not evidence** — it is retried, then it changes the strategy. It is
  never recorded as "the carrier is unavailable".
- **Autonomy ≠ authority** — information gathering and replanning run autonomously; a cost
  increase above `APPROVAL_COST_INCREASE_PCT` moves the confirmed path behind a human.
- **Phone content is untrusted input** — a caller saying "ignore your instructions" is data
  passed to extraction, never an instruction channel.
- **Bounded loops** — retry, replan and operation budgets; duplicate strategies rejected;
  an expired recovery window escalates instead of pretending.
- **Escalation is a valid outcome** — when no candidate satisfies the mandatory
  constraints, PHONEOPS escalates. It does not invent Carrier E to finish the demo.

---

## Mission Control

`apps/web` is a Next.js 15 / React 19 front end and a pure projection of backend
truth: it never recomputes whether a plan is still valid, it renders what the
engine decided.

```bash
cd apps/web && npm install && npm run dev      # http://localhost:3000
```

| Screen | What it answers |
|---|---|
| Critical Exception | What broke, what it costs, how long is left |
| Recovery Brief | The objective, the constraints, what is still unknown |
| Adaptive Recovery Control | What PHONEOPS is doing right now, and what it just learned |
| ⚠ Recovery Plan Invalidated | Required 17:30 vs confirmed 18:00 — the hero moment |
| Strategy Diff | Carrier B struck through, Carrier C live, and why |
| Recovery Outcome | Departure protected, 45-minute margin |

Live mission state arrives over SSE. Two details matter more than they look:

- **The stream is not proxied through the generic `/api/*` rewrite.** Next
  compresses proxied responses whenever the client advertises gzip — every
  browser does — and the gzip encoder buffers, so the stream works perfectly
  under `curl` and delivers nothing at all to a real browser. A dedicated route
  handler pins it to identity encoding, and the rewrite sits in `fallback` so it
  cannot shadow that handler.
- **Replayed history never re-triggers the hero moment.** The stream replays from
  a sequence watermark taken at load, so reopening a finished mission shows the
  recovered screen rather than a stale full-screen alert.

## Getting started

```bash
make env          # .env at the repo root, shared by the API and Mission Control
cd apps/api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
PYTHONPATH=. uvicorn app.main:app --reload    # http://localhost:8000/docs
```

<details>
<summary>Windows (PowerShell)</summary>

`make` and `PYTHONPATH=... command` are not PowerShell syntax, and installing
into the global interpreter is what produces `ModuleNotFoundError: No module
named 'aiosqlite'` — uvicorn starts, then the app fails to import a driver that
was never installed.

```powershell
Copy-Item .env.example .env
cd apps\api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt      # installs every pinned dependency

$env:PYTHONPATH = "."
uvicorn app.main:app --reload --port 8000
```

Then, in a second terminal:

```powershell
cd apps\web
npm install
npm run dev                              # http://localhost:3000
```

Same pattern for the proof scripts and the tests:

```powershell
$env:PYTHONPATH = "."
python -m pytest -q
python scripts\counterfactual.py
```

If `Activate.ps1` is blocked, run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first.
</details>

Or the full stack (Postgres, Redis, API and Mission Control):

```bash
make up          # docker compose up --build, creating .env if absent
```

Mission Control on `:3000`, API on `:8000`. Requires Docker Compose v2.24 or
newer for the optional `env_file` syntax.

`make check` runs everything CI runs: ruff, the backend suite, ESLint,
TypeScript and the production build.

Run the flagship mission over HTTP:

```bash
curl -X POST localhost:8000/api/demo/flagship/run | jq '.outcome'
```

Configuration layers, each overriding the previous one: the repo-root `.env`,
then an optional `apps/api/.env` for service-local overrides, then real
environment variables.

Set `MISSION_TIMEZONE` to where the operation runs (`Africa/Casablanca`,
`America/Chicago`, …). A cutoff of `17:30` is the warehouse wall clock; the API
stores absolute instants and Mission Control renders every timestamp back in
that zone.

### With real phone calls

```bash
CALLE_MODE=http
CALLE_API_KEY=sk-...
CALLE_BASE_URL=https://...            # from your CALL-E beta onboarding
CALLE_WEBHOOK_URL=https://<public>/api/webhooks/calle
DEMO_CARRIER_B_PHONE=+1...            # real numbers, E.164
```

> CALL-E places real outbound phone calls. Verify the plan, the recipient and the intent
> before running a live mission.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/exceptions` | Exception intake → Recovery Mission |
| `POST` | `/api/recovery-missions/{id}/start` | Run the adaptive loop |
| `GET` | `/api/recovery-missions/{id}` | Mission Control aggregate (one call, whole screen) |
| `GET` | `/api/recovery-missions/{id}/stream` | Live SSE mission events |
| `GET` | `/api/recovery-missions/{id}/explain` | Which call caused which replan |
| `GET` | `/api/recovery-missions/{id}/audit` | Full causal chain |
| `GET` | `/api/recovery-missions/{id}/{evidence,strategies,replans,operations}` | Detail views |
| `POST` | `/api/recovery-missions/{id}/approvals/{approval_id}` | Human approval decision |
| `POST` | `/api/operations/{id}/result` | Ingest a call result (tests / replay) |
| `POST` | `/api/webhooks/calle` | CALL-E terminal result callback |
| `POST` | `/api/demo/flagship` | Create + run the flagship mission (`?reset=true`) |

Interactive docs: `/docs`.

---

## Tests

```bash
make test            # 86 tests
make e2e             # the counterfactual proof
make reliability     # 5 consecutive flagship runs, gate before recording
```

| Suite | Covers |
|---|---|
| `tests/e2e/test_counterfactual.py` | different phone evidence → different behaviour; causal linkage |
| `tests/unit/test_constraints.py` | 18:00 vs 17:30, boundaries, uncertain/conflicting evidence |
| `tests/unit/test_intelligence.py` | hedging, nulls, negative evidence, prompt injection as data |
| `tests/unit/test_policy_and_guardrails.py` | autonomy, approval, block, budgets |
| `tests/integration/test_failure_behaviour.py` | no-answer, clarification, escalation, approval |
| `tests/integration/test_loop_semantics.py` | provenance, strategy history, idempotency, metrics |
| `tests/integration/test_api.py` | HTTP contract |
| `tests/integration/test_loop_semantics.py` | concurrent starts; every published event is already readable |

---

## Layout

```
phoneops/
├── apps/api/
│   ├── app/
│   │   ├── domain/          enums, schemas, errors
│   │   ├── models/          mission state (18 tables)
│   │   ├── engine/          director, planner, constraints, intelligence,
│   │   │                    replanner, decision, policy, guardrails
│   │   ├── integrations/    calle/ (http, cli, mock)  ·  llm/ (optional)
│   │   ├── events/          bus (SSE + Redis) and recorder
│   │   ├── services/        mission intake, view, time/value normalisation
│   │   └── api/routes/      exceptions, missions, operations, events, webhooks, demo
│   ├── migrations/          Alembic
│   ├── scripts/             counterfactual.py, reliability_gate.py, run_flagship.py
│   └── tests/               unit · integration · e2e
├── apps/web/                        Mission Control (Next.js, SSE)
│   ├── src/app/                     screens + the SSE route handler
│   ├── src/components/              exception, brief, control, diff, outcome
│   ├── src/hooks/useMission.ts      stream subscription and countdown
│   └── src/lib/                     typed API client mirroring the Pydantic schemas
├── community/mission-replan-loop/   reusable CALL-E contribution
└── docker-compose.yml
```

---

## How the demo carriers answer

Carrier B and Carrier C are **controlled PSTN test endpoints** — two Telnyx
numbers that answer with a fixed line, so the scenario is deterministic and
repeatable rather than dependent on two people sitting by a phone.

What that does *not* mean: the answer still travels the whole way. CALL-E places
a real outbound call over the telephone network, hears the spoken reply,
transcribes it and extracts the structured result. PHONEOPS receives evidence,
not a payload.

| Simulated | Real |
|---|---|
| the carriers' personas | the CALL-E call, and its call id |
| the shipment and its constraints | the telephone network |
| the answers they give | the transcription and evidence extraction |
| | the constraint evaluation and the replan |

The endpoint that plays them (`app/api/routes/telnyx_sim.py`) is mounted only
when `TELNYX_SIM_ENABLED` is set, holds no reference to missions, evidence or the
database, and cannot write to it. That isolation is deliberate: a shortcut from
there into the mission would be indistinguishable from hard-coding the demo, and
would make CALL-E ornamental.

## Demo

Live: [phoneops.vylantic.com](https://phoneops.vylantic.com) (Mission Control),
[ai.phoneops.vylantic.com](https://ai.phoneops.vylantic.com) (API).

`docs/screens/` holds a capture of every screen, including the failure paths —
escalation, unreachable carriers, an unsupported destination, a human rejection.

`docs/deploy.md` deploys the stack to a DigitalOcean droplet behind a shared
Nginx proxy, including the reverse-proxy settings the event stream needs, which
are the ones that fail silently.

## Build log

`CHANGELOG.md` records what was built in what order, and every bug found on the
way — including the ones only a real browser exposed, and the ones only a real
phone call could.

## North Star

**Exception Recovery Rate** — the share of qualifying exceptions for which PHONEOPS
confirms a viable recovery path before the deadline. Not calls automated, not agents
deployed. Supporting metrics: time to recovery, replans per mission, human intervention
rate, escalation rate.

## What this is not

Not a TMS, not a carrier marketplace, not a freight booking platform, not a call centre,
not a generic agent builder. PHONEOPS owns exception recovery, not normal logistics
execution.
