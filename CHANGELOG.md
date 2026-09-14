# Build log

No git history ships with this archive, so this is the record of what was built,
in what order, and — more usefully — what was found broken and why it mattered.
File modification times in the archive are the real edit times; sort by date to
see the same sequence.

## 1 — Backend (`apps/api`)

FastAPI recovery engine: domain, mission state (18 tables), constraint engine,
planner/replanner, CALL-E adapter in three modes, event bus with SSE, REST API,
Alembic migration, and the proof scripts (`counterfactual.py`,
`reliability_gate.py`, `run_flagship.py`).

Bugs found by the tests while building it:

- `parse_number("2,000")` returned `2.0` — the thousands separator was read as a
  decimal point, which silently broke the procurement template.
- The golden path ended in `approval_required`: selecting a recovery path is a
  recommendation, not a commitment, so `confirm_recovery_path` belongs in the
  autonomous set.
- `cost_increase_pct` never reached the policy engine because it was missing from
  the `result_schema` sent to CALL-E.
- `human_interventions` was always `0` — the view counted only *pending*
  approvals.

## 2 — Mission Control (`apps/web`)

Next.js 15 / React 19 / Tailwind v4. Five screens, the invalidation overlay, the
strategy diff, the approval panel. Palette derived from the mark (navy `#031335`,
electric blue `#024BFA`); Barlow and IBM Plex Mono, self-hosted so the demo runs
offline.

## 3 — What driving a real browser exposed

None of these were visible from `curl` or from the test suite.

- **Next gzipped the SSE stream.** The `/api/*` rewrite compressed the proxied
  response whenever the client advertised gzip — every browser does — and the
  encoder buffers, so the stream delivered nothing to a browser while working
  perfectly under `curl`. Fixed with a dedicated route handler pinned to identity
  encoding.
- **The rewrite shadowed that handler.** `afterFiles` rewrites are matched before
  dynamic routes; moved to `fallback`.
- **Events were published before commit.** `record_event` flushed and published,
  so a subscriber was told "something changed" about state no other transaction
  could read. The UI spent its only signal, re-read the pre-event state and froze
  there. Now each event commits before it is published — which also means a
  mission that crashes mid-run keeps the record of the CALL-E calls it really
  placed. Pinned by `test_every_published_event_is_already_readable`.
- **The hook dropped the last refresh.** Concurrent refreshes were discarded
  instead of coalesced, leaving `EVALUATING` under a `RECOVERED` banner.
- **Replayed history re-triggered the hero moment.** The stream replayed from
  `since=0`, so reopening a finished mission pinned a full-screen "PLAN
  INVALIDATED" alert over a recovered mission. Now streamed from a sequence
  watermark taken at load.
- **`start_mission` had a TOCTOU race.** Reading and writing `started_at` were two
  separate awaits, so a double-clicked START RECOVERY ran the mission twice and
  duplicated the audit trail. Replaced with an atomic conditional update.
- **The test harness was not proving isolation.** In-memory SQLite is served
  through a `StaticPool`, so "independent" sessions shared one connection. Moved
  to a file-backed database.

## 4 — Infrastructure

`docker-compose.yml` (postgres, redis, api, web), a `Dockerfile` for Mission
Control, `.dockerignore` for both services, CI, MIT licence, Makefile targets for
both halves, and a Windows/PowerShell section in the README.

Found during the audit:

- The compose file built only the API — no `web` service, no frontend image.
- `docker compose up` failed on a fresh clone: `env_file` is mandatory by default
  and only `.env.example` is committed. Now optional, and `make env` creates it.
- `npm run lint` was a broken promise: `next lint` with no ESLint dependency and
  no config. ESLint 9 and a flat config now run clean.
- `sse-starlette` was pinned but never imported.
- `tsconfig.tsbuildinfo` was shipping in the archive.
- **Env file precedence was inverted.** pydantic-settings gives priority to the
  *last* file, so the repo-root `.env` silently overrode `apps/api/.env` — the
  closest, most specific file. The order is now
  `("../../.env", ".env")`, pinned by `tests/unit/test_config.py`.

## 5 — Timezone

Reported from a browser one hour east of the server: the header read
`Cutoff 18:30` directly above `Pickup must occur before the warehouse cutoff ≤
17:30`, which destroys the demo's whole argument — 18:00 no longer looks like a
violation.

`today_at("17:30")` combined the wall clock with UTC, so the instant was 17:30Z
and the browser rendered it in local time. Constraint values are plain
wall-clock strings with no offset, so they did not shift, and the two
contradicted each other. Both containers here run UTC, which is exactly why no
browser test caught it.

Fixed by making the frame explicit: `MISSION_TIMEZONE` (default `UTC`) is the
zone a cutoff belongs to, `today_at` builds the instant in that zone, the API
returns it on `deadline.timezone`, and every timestamp in Mission Control is
formatted in it. Pinned by `tests/unit/test_timezone.py` across four zones, and
verified in a browser set to `Africa/Casablanca`.

## 6 — Copy that had gone stale

- The Recovery Brief still ended with "PHONEOPS needs external confirmation
  before this plan can be validated" after every fact had been confirmed. That
  line dated from when the brief only appeared before the first call; it is now
  conditional.
- `cost_increase_pct` rendered as "0" under the label "Cost Increase Pct". The
  unit belongs on the value: "Cost Increase 0%".
- A `CONFIRMED` strategy still listed its facts under "Need to confirm".

## 7 — What the timezone fix itself broke

Reported from Windows: the API returned 500 on the first mission.

- `zoneinfo` has no zone database on Windows, so every lookup fails — and the
  fallback I had written was `ZoneInfo("UTC")`, which raises the very error it
  existed to absorb. It now returns `datetime.UTC`, which cannot fail, and
  `tzdata` is a pinned dependency so lookups work on Windows and on slim images.
- With that fixed, a non-UTC mission still broke in two places. The constraint
  was derived by formatting the stored UTC instant, so a 17:30 Casablanca cutoff
  produced `pickup_time ≤ 16:30`; every carrier then failed and the mission
  escalated. And a carrier's "16:45" was anchored on the instant's UTC day and
  offset, turning a 45-minute margin into a 15-minute overrun. Both now go
  through the operation's clock.
- The demo seed rolls the cutoff to the next occurrence of 17:30. An expired
  window correctly escalates without calling anyone, but a judge opening the
  demo at 18:00 local would read that as a broken product. Only the seed rolls
  the date; the engine still treats a past deadline as past.

Every test in the suite ran in UTC, where a wall clock and its stored instant
are the same number — which is exactly why these passed 98 green tests and still
broke. The loop now runs under Casablanca, New York and Tokyo as well.

## 8 — Timestamps without an offset

Reported: the timeline read 09:26 while the countdown put the mission clock at
10:27 — an hour apart on the same screen.

SQLite has no timezone type, so instants come back from the database naive.
`cutoff_at` was the only field re-stamped by hand, so it serialised as
`...Z` while all 71 other timestamps went out bare. `new Date("2026-09-06T10:29:05")`
is parsed by the browser as *local* time, so every one of them shifted by the
operator's UTC offset.

Response schemas now use a `UtcDatetime` alias that stamps naive values on the
way out. `tests/integration/test_serialization.py` walks the entire serialised
payload rather than a list of fields, so a new timestamp cannot slip through;
reintroducing the bug fails it with all 71 paths named.

## 9 — Last two stale labels

- A `COMPLETE` phone operation still listed its facts under "Discovering", as
  if the call were still running. It reads "Discovered" once the call lands.
- The brief said the same thing twice: "Missing information — Everything needed
  has been confirmed", then "Every fact above was confirmed by phone, not
  assumed". The list now reads "None outstanding" and the closing line carries
  the provenance point on its own.

## 10 — A metric that understated itself

"Time to recovery" read "1 min" for a recovery the engine measured at 3 seconds:
the UI rounded to minutes with a floor of one. That is the supporting metric for
the North Star, and it was misreporting the demo by a factor of twenty. Durations
now render at the precision they actually have — seconds under a minute, minutes
under an hour, hours above.

## 11 — An ask that did not match its contract

The call card listed three discovered facts while the evidence feed showed four
from the same call. `cost_increase_pct` is injected into every `result_schema`,
but the operation recorded only the constraint-derived list — so the API
reported an ask that was not the contract CALL-E received. Provenance that does
not add up is worse than none, given what this product claims.

`facts_asked` now feeds both, and an integration test asserts the stored ask
equals the schema's `required` set on every persisted operation. Unit-testing
the two helpers in isolation was not enough: the divergence lived in the caller,
and those tests passed with the bug reintroduced.

Fixing it exposed a second defect. The extractor had a safety-net branch that
appended cost on top of the main loop, harmless only while cost was never in the
requested list. Once it was, the same answer became two evidence records from
one call. The branch is now guarded, and both carriers report cost symmetrically.

## 12 — Evidence invented from silence

Once cost was asked on every call, a carrier that never mentioned it produced an
evidence card reading "— NEEDS CONFIRMATION", and the discovered-facts count rose
to 8 for 7 answers.

The distinction the engine was missing: a null on a *constraint* fact is
uncertainty the mission must resolve — it blocks satisfaction and warrants a
clarification call, and that behaviour is unchanged. A null on a *standing* fact
is silence. Nothing is measured against it, so recording it attributes to the
carrier an answer they never gave. Standing facts now produce no record when
unanswered; the call card names them as "asked, not answered" instead.

## 13 — One duration format

Adding `duration()` for time-to-recovery left the outcome margin on the old
`minutes()` helper, so the same screen showed "45 MIN" and "6 s". Both now use
`duration()`; `minutes()` had no other caller and is gone.

## 14 — One register in the timeline

The timeline mixed `available = True` and `Required pickup_time <= 17:30;
observed 18:00 -> VIOLATED.` with English sentences. Event *titles* now read as
operational language:

```
Carrier B availability: yes
Carrier B pickup time: 18:00
Carrier B cannot meet the pickup cutoff — 18:00, required 17:30 or earlier
```

The audit substrate is untouched: `ConstraintEvaluation.explanation` still
records the engine's exact comparison, and every event still carries constraint,
operator, required and observed in its payload. Precision moved out of the title
and into the fields built to hold it.

Evidence lines name their subject, because a timeline is scanned out of order
and two carriers answering the same question should not be told apart by
position. Satisfied checks drop a comparison that repeats itself ("meets the
capacity requirement", not "— yes, required yes").

`app/services/wording.py` is the single source: the short phrase reaches the UI
as `ConstraintOut.short_label`, and the duplicate map the frontend was keeping
is deleted, so the hero moment, the strategy diff and the timeline cannot drift
apart.

## 15 — The same consolidation, finished

Normalising the timeline exposed that the previous step was half done. The
timeline said "Carrier B availability: yes" while the card beside it said
"Available", and the evidence cards said "Capacity Ok" where the timeline said
"capacity" — two vocabularies on one screen, from a backend `fact_label()` and a
frontend `humanKey()` deriving names independently.

`RecoveryView.fact_labels` now serves the canonical wording for every fact key
the mission shows, and the UI capitalises it for standalone labels rather than
inventing its own. The causal line under a phone operation reads like the
timeline entry it cites: "Carrier B pickup time: 18:00", not "pickup time =
18:00". A test asserts wording is served for every key that reaches the screen,
so a new fact cannot arrive unworded.

## 16 — A rationale that stayed vague

Everything around the strategy swap named the requirement that failed — the
timeline, the invalidation moment, the diff — while the replan rationale beside
them said "Carrier B cannot satisfy the mission constraints". The verdict
already carried the constraint key; it just was not passed to the replanner. It
now reads "Carrier B cannot meet the pickup cutoff; Carrier C is the next
eligible candidate."

The timeline also mixed " - " and " — " in the same panel. One dash now, pinned
by a test.

## 17 — The failure paths, looked at for the first time

They were covered by tests but never rendered. Driving all four through a
browser found five defects the golden path could not expose.

- **An unreachable carrier was described as missing the cutoff.** The strategy
  diff read "Carrier B — 18:00 — Misses the pickup cutoff" for a carrier that
  never answered, and the "Why" line asserted a constraint failure that never
  happened. That is precisely the claim the product promises not to make. The
  diff now keys off the replan trigger: `call_failure` reads "no answer / Could
  not be reached".
- **An escalation rendered as green ticks.** `outcome.facts` carries a reason
  and an internal rule name for escalations, and the UI ran them through the
  confirmed-findings row — a green check against "no remaining candidate
  satisfies the mandatory constraints", and `no_viable_path` on screen. The
  reason now reads as prose, and "Attempted" lists the carriers actually called
  instead of an em dash.
- **The approval panel dumped raw JSON** across the row a human reads before
  committing money, and showed a 35% surcharge as "35".
- **A recovered mission still asked for confirmation**, because an abandoned
  carrier's unanswered questions stayed in the brief. Needs are now scoped to
  the path in play.
- The strategy card showed the engine's raw comparison, and the timeline a raw
  `no_answer` token.

## 18 — A deadline with only one end

Reported from a run at 17:01: the mission confirmed a recovery on a **16:45**
pickup — sixteen minutes in the past — and reported a 45-minute margin. The
constraint was `pickup_time <= 17:30`, which every past time satisfies. A
deadline is a window with two ends and the engine only checked the far one.

`lte`/`lt` time constraints are now bounded by the present: a collection that
can no longer happen is VIOLATED, which drives a replan instead of a false
recovery. `gte` is untouched — "no earlier than" is not made impossible by the
clock.

Two consequences worth naming:

- **The suite was time-dependent.** Enforcing the rule turned 19 tests red,
  because they assert that a 16:45 pickup satisfies a 17:30 cutoff and the
  container clock had passed 16:45. A suite that is green in the morning and red
  in the evening is worse than none, so `timeutil` gained a clock seam and the
  tests pin "now". `scripts/counterfactual.py` pins it too: the proof compares
  two runs of one mission and must not also depend on the hour.
- **The demo seed rolls further.** Moving to tomorrow only once 17:30 had passed
  left a 45-minute window in which the winning carrier's own pickup was already
  behind us and the flagship escalated. It now rolls when the earliest scripted
  time has passed.

## 19 — Looking for the same shape elsewhere

The past-pickup defect was a bound checked on one side only. Five candidates of
that shape were probed; two were real.

- **An open window is not a workable one.** `check_deadline` refused only an
  *expired* deadline, so with sixty seconds left PHONEOPS started a mission and
  placed real outbound calls to real people about an outcome it could no longer
  affect. `MIN_RECOVERY_WINDOW_SECONDS` now escalates instead, under its own
  rule `window_too_short` so the audit trail keeps the two refusals apart.
- **Windows that cross midnight were misjudged.** Every wall-clock answer was
  anchored on the cutoff's own date. With a 02:00 cutoff, a carrier saying
  "23:00" — three hours before it — was placed twenty-one hours after and
  refused. Answers now anchor on whichever adjacent day lands them nearest the
  cutoff, which is the cutoff's date whenever the window does not straddle
  midnight. Night operations are where a phone recovery matters most.

Three came back clean: stale evidence already fails to satisfy a constraint, a
cost *decrease* is correctly autonomous, and the flagship runs end to end at
23:59.

## 20 — What the roll-forward left behind

Running the demo at 18:13 showed the cutoff rolled to tomorrow and working — and
the screen reading "Cutoff 17:30" above a 23-hour countdown, under "Carrier A
cancelled today's pickup" and "Tonight's shipment departure". The fix for one
problem had created a smaller one.

- A wall clock alone is unambiguous only while the cutoff falls on the current
  operational day. It shows "Tomorrow 17:30", or a weekday beyond that. This is
  not only a demo concern: overnight windows are now supported by the engine,
  and they hit exactly the same ambiguity in production.
- The seed's copy follows the date it rolls to — "tomorrow's pickup", "Tomorrow
  evening's shipment departure". Leaving it behind made the demo assert
  something plainly false.

## 21 — Copy that assumed the operation ran today

With the scenario rolled to tomorrow, one line had not followed: "Carrier must
be available **today**". Chasing it found the same assumption baked into the
templates — and in the one place where it is not cosmetic.

`"Can you collect {entity} today?"` and `"{entity} must leave tonight"` are the
words CALL-E speaks to a real person. Asked about a window that closes tomorrow,
they get an accurate answer to the wrong question, and the whole recovery is
then built on it. The templates take a `{day}` resolved from the mission's own
cutoff — "today", "tomorrow", or a weekday — and the constraint labels stopped
naming a day at all ("available on the pickup day"), since they are timeless.

Worth noting how it was found: the first fix patched the prompt but not the
questions stored on the operation, and the two disagreed. The test caught it
because it asserts on the persisted record rather than on the string the code
happened to build.

## 22 — A timeline that spans midnight

The engine now supports overnight windows, so a recovery can run 23:58 → 00:05.
The timeline rendered bare clocks either side and the audit trail read as if
time had gone backwards. It now marks the day boundary once, where it happens.

The marker had to be forced to be seen: a real run stays inside one day, so the
branch would have shipped unrendered. Intercepting the API response to push half
the events past midnight exercised it — and caught that my first check for "a
marker" matched any letters in the panel, which every event title has.

## 23 — Time to recovery, re-anchored

`time_to_recovery_seconds` measured `completed_at - started_at`: the engine's
own execution time. It now measures `completed_at - detected_at` — how long the
operation was exposed.

Three reasons, in order of weight. It is the only definition that composes with
the North Star, which asks whether a viable path was confirmed before the
deadline; the supporting metric should say how much of the window was consumed.
It is the only one that can show the realistic production failure — a mission
nobody launched for forty minutes — which an engine figure structurally hides.
And "Time to recovery: 4 s" on the outcome screen invites precisely the
suspicion the whole submission fights.

`engine_seconds` keeps the old figure in the API for regression tracking, and is
deliberately absent from Mission Control: two durations no one can tell apart
are worse than one.

The change had to be made twice — the director computes metrics for the stored
outcome, the view recomputes them for the API — and the first pass missed the
second site, which is why the test asserts through the view.

## 24 — The beat the demo script requires, and could not film

Checked the product against the 2:45 script in the spec. The script holds; the
product had a gap against it.

DOCUMENT 05 §16 makes the sponsor-necessity moment mandatory — before the first
call, show the recovery blocked on information no connected system holds — and
§47 gives it twenty seconds (00:15–00:35). Two things prevented filming it:

- Opening a mission without `?autostart=1` left it in `assessing` with **no way
  to start it**. The only path ran the loop immediately, so the brief was on
  screen for a fraction of a second.
- Worse, it had nothing to show. Information needs were registered inside
  `start_mission`, so the brief read "None outstanding" until CALL-E was already
  dialling — on the one screen whose whole job is to prove the opposite.

Planning now happens at intake (`prepare_mission`), so a mission carries its
first strategy and its open questions before anyone starts it, and Mission
Control holds that state with a Start control. The brief's closing line follows:
"No connected system holds these answers" before the calls, "Every fact above was
confirmed by phone, not assumed" after them.

## 25 — The shooting script was never in the repo

It lived only in the spec PDF. The product had been checked against it, but a
judge cloning the repo — or anyone recording the video — had nothing.

A shooting script now holds it: beat timings, the exact on-screen text at
each one read off a real run rather than copied from the spec, the voiceover,
the truthfulness table, and the recording constraints that only exist because of
what was built since — record before 16:45 local or the scenario rolls to
tomorrow, open the mission without `?autostart=1` to hold the brief, click
promptly because time-to-recovery now counts from detection, and freeze-frame
the invalidation overlay because it dismisses itself after 3.2 seconds.

Where the product's wording has moved on from the spec's, the script quotes the
product and notes the difference.

## 26 — Checked against the actual rules

Read the official rules and the overview rather than working from the product
spec, and found one error that would have invalidated the submission.

**The pull request goes to `awesome-phone-call-agents`, not
`call-e-integrations`.** The latter is the setup guide. The overview page warns
about exactly this confusion, in a box, and the earlier plan had it wrong.

A compliance checklist now holds the matrix, the PR procedure against
their own naming conventions (`feat/mission-replan-loop`,
`feat(mission-replan-loop): add evidence-driven replanning skill`), their
mandatory `scripts/validate_repository.py` run, the README list entry in their
template, and testing instructions that work without a CALL-E key — which the
rules require, since judges must be able to test free of charge.

Two things the spec did not mention and the rules do: the video must be publicly
visible on YouTube or Vimeo (unlisted does not qualify) and carry no copyrighted
music, and the Technical Implementation criterion asks for CALL-E "called at
runtime, not just referenced" — so the recording must run `CALLE_MODE=http`,
not mock.

One claim in the first draft of that checklist was wrong: it said the skill's
examples use fictional `+1555…` numbers. They contain no phone numbers at all —
the skill takes a target by name and never handles a number, since placing the
call belongs to the host. Stricter than the rule asks, and now stated correctly.

## 27 — Devpost copy

The submission copy is written: elevator pitch (186 of 200
characters), built-with tags, the long description, testing instructions, and
the remaining form fields.

Every figure in it was re-checked against the code rather than recalled — 146
tests, 17 tables, ~7,200 lines of Python — and the two strongest claims were
executed rather than asserted: the full loop recovers with `LLM_ENABLED=false`,
and a hedged `17:30` against a 17:30 cutoff comes back `uncertain` at low
confidence instead of satisfying it. The first draft said 18 tables, which is the
migration's count including `alembic_version`.

The description leads with the problem rather than the architecture, and the
"what's next" section names a real gap — candidate ranking is static, and should
be informed by which carriers actually answer.

## 28 — Deployment guide

`docs/deploy.md` puts the stack on a droplet behind a shared Nginx proxy:
`phoneops.vylantic.com` for Mission Control, `ai.phoneops.vylantic.com` for the
API.

Written against the repository's own artefacts rather than a template, and every
claim about them was checked: `output: "standalone"` and the `fallback` rewrite
in `next.config.ts`, `CMD ["node", "server.js"]`, the `HEALTHCHECK` in both
images that `depends_on: service_healthy` relies on, and the exact `/api/health`
payload the verification step quotes.

Three things a generic guide would miss:

- **Nginx must not compress or buffer the event stream.** This is the same defect
  that cost a day locally: it works perfectly under `curl`, which sends no
  `Accept-Encoding`, and delivers nothing to a browser, which always does. The
  verification step therefore runs the stream twice, with and without the gzip
  header, and expects the same event count.
- **The public demo endpoint places real calls.** `POST /api/demo/flagship` is
  unauthenticated; with `CALLE_MODE=http`, any visitor makes real phones ring on
  the entrant's credits. The guide recommends mock mode for a judge-facing URL,
  and gives two other options.
- **The dev compose is not a production compose.** It publishes 5432 and 6379 on
  the host and hard-codes the database password. The guide ships a separate
  production overlay and tabulates the differences rather than implying the
  committed file is deployable.

It also states plainly that the images have never been built here, and what was
verified instead.

## 29 — A flow the script described but the product did not have

Reported: the demo script says to open the mission without `?autostart=1`, but
the landing page's only button navigates *with* it. The instruction was not
followable — the landing page is the only way to obtain a mission id, so the
pre-flight brief added in §24 was unreachable through the UI.

The landing page now **opens** the mission; **Start recovery** on the brief
launches the loop. Two deliberate acts, matching the spec's own screen order,
and the script's instruction is now simply the product's behaviour.

The same report, run with `CALLE_MODE=http` and the placeholder `+1555…`
numbers, exercised an escalation shape the earlier probe never did — every
carrier unreachable — and found three defects:

- The invalidation overlay, the loudest screen in the product, rendered
  `REQUIRED ≤ —` over "Carrier D cannot meet the pickup cutoff" for a carrier
  that never answered. The same fabricated comparison fixed in the strategy diff
  in §17, in a component that had not been revisited. It now reads "no answer".
- The diff's NOW panel said `VALIDATING` about a strategy the card beside it
  showed as `INVALID`.
- An escalated mission still listed nine open questions and claimed it "needs
  external confirmation".

The demo script and the deployment guide now both state what `http` mode
implies: the scripted personas are a mock feature, so real numbers and briefed
people are required, or every call goes unanswered and you film the escalation.

## 30 — The same questions, three times

Reported on the pre-flight screen: the open questions appeared twice. They
appeared three times — the dedicated panel, the fallback that still rendered
when no call existed, and the brief's own "Missing information" column, each in
different wording. On the one beat whose job is to be unmistakable.

The panel is now the single rendering. The fallback is suppressed in pre-flight,
and the brief drops its column when the panel is present.

The same screen also labelled the plan `VALIDATING` before any call existed.
Nothing was being validated; the strategy is merely `PROPOSED` until a call is
out. The engine still records it as active — that is accurate, it is the plan
being pursued — so the card derives the label from whether an operation exists,
rather than changing state the engine is right about.

## 31 — Auditing every screen, not inspecting them

Eight screen states driven through a browser with generic structural checks
rather than by eye: a panel heading rendered twice, a machine token, a malformed
value like `≤ —`, and a status chip contradicting the banner above it.

Seven came back clean. The approval screen did not, and the defect was one no
spot check would have phrased as a bug.

**The screen asked an operator to approve a path it described as unverified.**
Carrier C had answered, every constraint passed, and the timeline read "Recovery
path confirmed" — while the strategy card said `NEED TO CONFIRM`, the diff said
`Needs verification`, and three chips said `VALIDATING`. What was pending was a
human decision, not more checking. All three now read `AWAITING APPROVAL`, with
the facts ticked as confirmed.

It took two passes: the card and the diff were fixed first, and the audit still
failed because the strategy-history row rendered the same stale label. Counting
occurrences rather than checking presence is what caught it.

One honest limitation: the "call in flight" state was captured after the loop
had already finished, so that state is asserted by the earlier probes, not by
this audit.

## 32 — Text written for a live mission, shown on a dead one

The all-unreachable escalation carried four of these, and the worst contradicted
the metrics panel on the same screen:

- **"Every fact above was confirmed by phone, not assumed"** above
  `Facts discovered: 0`. Nothing was confirmed — no carrier answered. The line
  now keys off evidence actually obtained, and reads "No carrier could be
  reached, so nothing was confirmed."
- A `FAILED` phone operation still headed **"Discovering"**, and still said it
  was **"retrying after an unanswered call"** minutes after the mission ended.
- An `INVALID` strategy listed three facts under **"Need to confirm"**. Nothing
  was going to be confirmed; they were never established.
- The evidence panel said **"No external facts yet … until CALL-E answers"** on
  a mission that had stopped calling.

The previous audit passed this screen because it checked for contradictions it
knew about. Five checks were added for the present-tense class, and — since
adding assertions that cannot fail has been the recurring failure of this build —
two defects were reintroduced to confirm the audit catches them. It does.

## 33 — A duplication my own fix created

The previous turn made a failed call show its facts as "asked, not answered".
It did not stop showing the list above, so every fact appeared twice:

```
Asked, never answered
Availability · Pickup time · Capacity · Cost increase
Availability — asked, not answered
Pickup time — asked, not answered
…
```

The suffix block exists for a *partial* answer — a completed call that returned
some facts and not others. On a failed call nothing came back, so the list above
already is the unanswered set. It is now shown only when the call completed.

Also: an escalated mission reported a **"Time to recovery"**. It recovered
nothing. The row is now "Time to escalation" when the outcome is not a recovery.

### The detector needed two attempts

A generic "repeated line inside a panel" check produced forty false positives —
three constraints each reading `SATISFIED`, seven evidence cards each reading
`CONFIRMED`. Those are list rows, not duplication.

Worse, it would not have caught the defect it was written for: "Availability"
and "Availability — asked, not answered" are different strings. The check now
matches a fact as a *line prefix*, and only inside the phone-operation panel
where each fact must appear once. Verified by reintroducing the bug: it names
all three.

## 34 — Blaming constraints for a phone that never rang

The all-unreachable escalation reported **"No remaining candidate satisfies the
mandatory constraints"**, above a metrics panel reading `Facts discovered: 0`.
Nothing had been evaluated against a constraint; nobody answered. The replan
rationale said the same thing — "Carrier C cannot satisfy the mission
constraints" about a carrier that was never reached.

This is the defect already fixed twice in the interface — in the strategy diff,
then in the invalidation overlay. Both times the UI was inventing the claim.
This time the backend was writing it, into the audit record.

Both sentences now follow the cause. The exhaustion reason checks whether any
evidence was ever obtained; the replan rationale takes the trigger. Two
escalations, two causes, two sentences:

```
all unreachable     No candidate could be reached.
                    Carrier C could not be reached; Carrier D is next.
all past the cutoff No remaining candidate satisfies the mandatory constraints.
                    Carrier C cannot meet the pickup cutoff; Carrier D is next.
```

Two tests pin both shapes — the second exists so fixing one does not collapse
the other into it — and the audit gained a check that the reason matches its
cause. Verified by reintroducing both claims: one test fails.

## 35 — The Reject button, rendered for the first time

The approval panel has two buttons and only one had ever been exercised.

Two corrections to make first, both mine. I reported that **Reject did nothing** —
it does; my probe clicked with `text=Reject`, which resolved to an ancestor, so
no request was ever sent. And a second check failed on case-sensitivity, not on
the product. Clicking by role, the mission replans to Carrier D and recovers,
with the rejection recorded as a human intervention.

The screen itself carried a real defect, the same shape as the last two turns.
A carrier that **answered, satisfied every constraint and was declined on cost**
is neither in breach nor unreachable — but there were only two narratives, so it
was shown as "no answer / Could not be reached", directly above a line reading
"Operator rejected Carrier C". The replan rationale said it "cannot satisfy the
mission constraints", which it plainly did.

Three causes, three sentences now: breached, unreachable, declined. The diff
shows the value the carrier actually gave, marked "Declined by an operator".

## 36 — The briefing that was asked for but never written

The demo script said the `http` recording needs "someone briefed to answer 18:00
and 16:45" and left it at that. A briefing was written for them:
one card per carrier, with the questions CALL-E actually asks — read off a real
run, not paraphrased — and what each answer must contain.

The instructions that matter are the negative ones, and each was verified against
the engine rather than assumed:

- **Do not hedge.** "Probably around six" comes back `uncertain` at low
  confidence, and the engine calls back to clarify. Correct behaviour, forty
  unscripted seconds in a 2:45 film.
- **Do not quote a surcharge.** Anything above `APPROVAL_COST_INCREASE_PCT`
  (15%) routes the recovery to the human approval gate.
- **Answer promptly.** A carrier is called twice in all, not retried twice — the
  first draft had that wrong and a real run corrected it.

## 37 — Every real call rejected with 422

First run against the live API with real Moroccan numbers: six calls, six
`422 Unprocessable Entity`, no phone ever rang.

**The payload was wrong.** `CreateCallRequest` (Developer API 0.7.0) is
`additionalProperties: false` over six fields, and takes `recipients` — a list,
each with `phones` as a list. We sent `recipient` with a single `phone`, a
contract derived from documentation early in the build and never checked against
the API. Confirmed against three independent sources, including the official
`calle-ai` SDK's own request builder rather than a second guess.

**Two defects made it worse than a typo.**

The 422 body was captured in the error details and never logged, so the operator
saw "could not reach Carrier B" and had nothing to act on. It is now logged at
error level with the status and body.

And the classification was wrong. A provider refusing the request is not a
carrier who did not answer — but every rejection was routed through the
unreachable path, so PHONEOPS walked the entire candidate list and escalated
with "No candidate could be reached". A plausible business story covering an
integration fault, which is the exact failure mode the product claims to avoid.
A 4xx now escalates immediately under `provider_rejected_request`, after one
attempt, saying no call was placed.

**Nothing tested the wire format.** Reverting the payload to the broken shape
left all 150 tests green: the mock accepts any shape, and the only component
that would object is the provider. `tests/unit/test_calle_contract.py` pins it
against the documented field set — three tests fail on the original payload.

## 38 — `result_schema_invalid`

With the payload shape fixed, the live API refused the schema instead:

```
400 result_schema_invalid
unsupported JSON Schema type at $.properties.available: ['boolean', 'null']
```

The new error handling did its job — one attempt, no candidate list burned, and
the provider's own message on screen instead of "could not reach Carrier B".

Union types are not supported. Neither are `$ref`, `oneOf`, `anyOf`, `allOf`,
`pattern` or `format`; `type`, `properties`, `required`, `enum`, nested objects,
simple `array.items`, `description` and `additionalProperties: false` are.

Rather than just dropping `null` from every union, the schema follows CALL-E's
own guidance — prefer a string enum with an explicit `unknown` member over a
boolean for anything a call may not settle. That suits this product better than
the original: an unanswered question must never read as "no", and the
confirmation flag gained a third state, `approximate`, which is what a hedge
actually is. "Probably around 5:30" is now a value the extraction model can
choose, not a boolean it has to guess at.

Extraction reads both vocabularies, so mocked runs — which still send booleans —
are unaffected.

Five tests pin the supported feature set, including one that walks the whole
schema for unsupported keywords. Two fail if a union type comes back.

## 39 — The calls go out, and say why they failed

`201 Created`, real call ids, polling working: the integration is live. Six real
calls were placed — and all six reported `failed` with no reason anywhere.

`failure_code` and `failure_message` live on each **attempt**, and nothing read
them. The same blindness as capturing the 422 body and never printing it, one
layer down: the provider was saying why, and PHONEOPS was discarding it.

Three consequences, now fixed:

- **The outcome was wrong.** `failed` covers busy, unanswered and a dead number
  alike, and the attempt's code is what distinguishes them — which is also what
  decides whether a retry is worth placing.
- **The reason never reached the operator.** The timeline read `CALL-E #001 —
  failed`; it now carries the provider's own message, and a warning is logged
  with the call id and target.
- **Dead numbers were redialled.** `invalid_number`, `blocked` and the like
  cannot change on a second attempt, and each attempt is a real charge. Those
  skip the retry.

Reverting the outcome mapping fails two tests.

## 40 — Morocco is not a destination CALL-E serves

Six real calls, `201 Created` every time, six `failed` with an empty reason.
The payload was right, the schema was right, the numbers were right. The
destination was not: CALL-E serves 42 countries and `MA` / +212 is not one of
them.

This is the one configuration error the integration could not discover by
reading its own request. A well-formed call to an unsupported country is
accepted, dialled, charged, and fails per attempt — which is why three rounds of
fixing the payload never reached it.

`regions.py` now carries the supported list, and an unsupported destination is
refused **before** dialling, classified as a rejection so the mission escalates
instead of spending the whole candidate list. The message names the region, the
number, the setting to change and where calls can actually be placed — a refusal
that leaves an operator somewhere to go.

`.env.example` defaults to `FR` with `fr-FR`, and the briefing leads with this
check rather than the wire test, which would have passed all along.

## 41 — Platform inability is not business evidence

The region check added last turn never fired: it validated the candidate's
declared `region` field — `"US"` — while the number was `+212`. A label, not a
destination. CALL-E routes on the number, so six calls still went out.

The check now derives the country from the E.164 prefix. And the taxonomy the
failure lands in matters as much as catching it:

| | recipient failure | execution failure |
|---|---|---|
| cause | no answer, busy, declined | unsupported destination, provider error |
| carrier | judged | **not evaluated** |
| strategy | invalidated | left standing |
| next | retry, then replan | escalate, change nothing about the carrier |

An unsupported destination now stops after one attempt, leaves the strategy
`active` rather than `invalidated`, and the card reads **NOT EVALUATED** instead
of `VALIDATING` — a carrier that was never called has not failed anything.

Eight browser checks on that screen: no evidence invented, no constraint blamed,
the unreachable destination named, and the setting to change stated.

## 42 — The proxy target was frozen at build time

First deployment: the site served, and every `/api/*` call returned `500` with
no body.

Next compiles `rewrites()` destinations into `routes-manifest.json` **when it
builds**. The Docker image is built without `API_ORIGIN`, so the target froze as
`http://localhost:8000` — the web container itself — and the variable supplied
by compose at runtime was never read. Confirmed by building with a sentinel
origin and reading the manifest back:

```
"destination": "http://build-time-value:9999/api/:path*"
```

The proxy is now a route handler that reads the environment per request, so one
image runs locally and in production. Verified by building with a wrong origin
and starting with the right one: it works.

Two things made this harder to diagnose than it needed to be:

- **The failure had no body.** An unreachable upstream now returns `502` naming
  the origin it tried and what to check, instead of a bare `500`.
- **The page told visitors to "start the API on port 8000".** A developer's
  instruction, shown by a deployed site. It reports the actual error now.

## 43 — Controlled PSTN endpoints for the demo carriers

Morocco is not a destination CALL-E serves, and two people sitting by two
phones is not a reproducible demo. `POST /api/webhooks/telnyx` answers CALL-E's
calls as Carrier B and Carrier C over a real US line.

It is a **test harness**, and the boundaries matter more than the feature:

- mounted only when `TELNYX_SIM_ENABLED` is set, asserted both ways;
- no reference to missions, evidence, constraints or the database;
- a number it was not asked to play is never answered.

The carrier's answer still travels the long way — spoken over the telephone
network, heard by CALL-E, extracted into a structured result. A shortcut from
here into the mission would be indistinguishable from hard-coding the demo, and
would make CALL-E ornamental.

Three details that decide whether the call is usable:

- **A three-second SSML pause before speaking.** CALL-E greets the instant the
  line is answered; without it both sides talk at once and the pickup time is
  the part that gets lost.
- **The time is spoken as a dispatcher would say it** — "6 P M", not "18:00",
  which a synthesiser reads as "eighteen hundred" and transcribes unreliably.
  It is repeated once, for the same reason.
- **Redelivered events are ignored.** Telnyx redelivers; speaking twice talks
  over the first line. The endpoint always returns 200 for the same reason.

Also fixed on the way: `DEMO_CARRIER_*_PHONE` was documented in `.env.example`
and read straight from `os.environ` in one place only. Both the seed and the
endpoints now read it from settings.

## 44 — Two defects in artefacts that had never been executed

First `docker compose up` of the full stack. Both failures were in files written
carefully and never run, which was stated at the time and is now demonstrated.

**`ModuleNotFoundError: No module named 'cryptography'`.** The Telnyx signature
check imports it; it was never added to `requirements.txt`. Present transitively
in development, so 176 tests passed and the container died on startup — after
the migrations had already run. An audit of every third-party import in `app/`
against the declared set found exactly one gap, and is now a test.

**`sh: --forwarded-allow-ips=*: not found`.** A folded YAML scalar keeps the
newline of a *more-indented* line, so `sh -c` received three commands instead of
one. Reproduced by parsing the guide's own YAML back:

```
'sh -c "alembic upgrade head &&\n       uvicorn … --proxy-headers\n       --forwarded-allow-ips=…"'
```

The committed `docker-compose.yml` carried the same trap and would have broken
the first `docker compose up` for anyone cloning the repository. Both are one
line now, with `exec` so SIGTERM reaches uvicorn instead of stopping at the
shell. Two tests parse the compose files and fail on a folded command.

## 45 — The phone chain works, and two things it exposed

First live end-to-end call: CALL-E dialled the Telnyx DID, `call.initiated` →
answer, `call.answered` → speak, fifteen seconds of audio. The hard part of the
project is behind it.

**The hangup never fired.** Telnyx sends `call.speak.ended` with `to: null`, and
the handler re-derived the carrier from each event — so it logged
"unknown_destination" and did nothing. The line stayed open for 52 seconds of
silence until CALL-E gave up, in the middle of what is meant to be a 2:45 film.
The carrier is now remembered against the `call_control_id` and recalled on
events that drop the number. The recall does not become "answer anything": an
unknown call with no number is still ignored, and that is asserted.

**A dropped webhook froze the mission.** The poller was disabled whenever
`CALLE_WEBHOOK_URL` was set, so the webhook was not the fast path — it was the
only path. The mission sat on `RECOVERING` with no way back. PHONEOPS now polls
as well; ingestion has been idempotent since §2, so the two paths cannot
double-count. The log says which role polling is in.

## 46 — The thesis, on a real phone line

CALL-E dialled the Telnyx endpoint, heard "6 P M", and PHONEOPS recorded
`Carrier B pickup time: 18:00`, failed it against the 17:30 cutoff, invalidated
the strategy and replanned to Carrier C. The product's central claim, executed
live rather than asserted.

Two things the run exposed.

**Two schema fields were not extracted.** The spoken line bundled availability
and capacity into one clause — "we are available today and we have capacity for
the full shipment" — and CALL-E took the capacity, returning `unknown` for
availability. Cost was not extracted either. Each field now gets its own short
sentence. On an unlucky run the missing field triggers a clarification call,
which is fourteen credits to be told something already said.

**Credits.** The hangup bug measured at 47 credits for one call against 14 for
the same call once fixed. A full golden path is roughly 28. The five-run
reliability gate would be 140 — more than a topped-up balance — so it belongs in
mock mode, where it proves what it is for: that the engine is deterministic. The
briefing now says so, with the numbers.

## 47 — The webhook 403, and why dropping the check was not the fix

CALL-E's callbacks were returning 403. Its own SDK gives the reason: the HMAC
helpers are marked deprecated and "current CALL-E webhooks are unsigned". We
were demanding a signature nobody sends.

Simply accepting the body would have been worse than the 403. An unauthenticated
endpoint that ingests call results lets anyone POST a carrier's answer into a
recovery plan — on a product whose entire claim is that every fact is sourced to
a call.

The delivery is now a **notification, not evidence**. An unsigned callback tells
us which call changed; the result is read back from CALL-E over the
authenticated API. A forged payload changes nothing, and a signing proxy in
front still works and skips the read-back.

Two of the first tests written for this passed for the wrong reason: in mock
mode the mission has already finished when the webhook lands, so ingestion is a
no-op and any assertion about resulting evidence holds regardless. They now
assert which source the handler trusted. Both fail if the body is believed.

Also: the availability field, the one CALL-E returned as `unknown` on the first
live call, is now stated as its own sentence naming the carrier — "Yes. Carrier B
is available today." — rather than folded into a clause about capacity.

## 48 — Submission artefacts, brought up to date

The copy was written many turns ago and had gone stale on every count: 146 tests
against 187, "nothing is deployed" against a live site, and — the one that
mattered — no mention anywhere of the Telnyx endpoints.

**The disclosure is now the first thing the README says about the demo.** Judges
discovering an undisclosed carrier simulator is the one avoidable way to lose
this, and stating it plainly turns it into evidence of knowing where the line
is: the personas are simulated, the call, the network, the transcription, the
evidence extraction, the constraint evaluation and the replan are not.

Also written: a submission for the separate feedback prize. It is
drawn from the integration rather than the documentation — the unsupported
destination accepted, dialled and billed; terminal failures with no
machine-readable reason; webhooks unsigned with the only clear statement of it
inside a deprecation docstring — and it says which problems were ours.

The shooting script still told the operator to brief two people by a phone.

## 49 — The contributed skill had not learned anything

`community/mission-replan-loop` was written from the design and never revisited,
while the product spent a dozen rounds learning things the hard way. The skill
*is* the hackathon contribution, so shipping it ignorant of all of it would have
handed the community a package that repeats every mistake.

Added, each earned rather than designed:

- **The recipient/execution failure split**, with the table and the failure mode
  it prevents: a configuration error walking the candidate list and reporting
  "no candidate could be reached" — a plausible business conclusion covering a
  platform fault, with no phone ever ringing. Two policy rules, a section in
  `safety-boundaries.md`, a `failure_class` field in the output contract, and a
  fifth worked example.
- **A deadline has two ends.** "Before 17:30" is satisfied by every past time.
- **How to ask for facts a caller can leave unanswered** — string enums with an
  explicit `unknown` over nullable booleans, three-state confirmation, one fact
  per sentence, and silence producing no record. All four cost a live call each.

## 50 — What their validator actually requires

`validate_repository.py` failed on `references/examples.md`. It stops at the
first error, so rather than fix one file and push again, the requirement was
read from the source: `validate_skills()` reads **two** files in `references/`
unconditionally — `safety.md` and `examples.md` — while their CONTRIBUTING.md
names only `SKILL.md`. Their own issue #452 is about that gap, and other PRs in
the repository were bounced for the same reason.

Our `safety-boundaries.md` was renamed to the required `safety.md`, every link
updated. `references/examples.md` is generated from `examples/*.yaml`, so the
prose and the fixtures cannot drift, and it opens with why each case exists
rather than listing five inputs.

`community/check_skill.py` runs their documented checks locally — required
files, frontmatter name against the directory, linked paths resolving,
English-only, fictional numbers, fixtures parsing. All pass.

## 51 — No README inside a skill

Second validator error: `Skill directory must not include README.md; move
long-form guidance to docs/`.

The skill's README held the counterfactual table — the one row where a 16:00
answer produces *no* second call, which is the whole argument for the skill
existing. Deleting the file would have deleted the argument, so it moved into
`SKILL.md`, along with the three-domain table showing the loop is not
logistics-specific. `tests/README.md` went too, since the rule is not limited to
the top level; what it explained now sits in `references/examples.md`, where
someone reading the fixtures will actually find it.

`community/check_skill.py` gained that rule and three more from the Agent Skills
spec the validator implements — the `name` pattern, and the length caps on
`description` and `compatibility`. Each validator round trip costs a push, and
the requirements are spread across a CONTRIBUTING guide that omits them, an open
issue, and other contributors' rejected PRs.

## 52 — Their frontmatter parser is not a YAML parser

Third validator error: `Invalid frontmatter line`, pointing at a line that is
correct YAML. The block is read line by line as `key: value`, so a folded scalar
(`description: >`) leaves continuation lines that match nothing.

The local checker had passed the same file, because it used `yaml.safe_load` —
it was validating YAML, while the validator validates something narrower. It now
models their parser instead of a correct one, which is the only version that is
useful. Verified by restoring the folded description: it fails.

Four validator runs, four pushes, three of them on requirements the contribution
guide does not state. That is now the strongest item in the feedback submission:
report every error in one run, and either parse the frontmatter as YAML or say
that it is not.

## 53 — Validator green, PR body written

`Repository validation passed.` Four rounds, three of them on requirements the
contribution guide does not state.

The PR is written: branch, commit and title in their naming
convention, the README resource-list entry in their template, and a description
built around the one row that matters — a 16:00 answer producing no second call.

Every checklist claim was verified rather than ticked. The skill ships no
scripts, so the dry-run requirement is answered by what it is rather than by a
flag; and it contains no phone numbers at all, because it takes a target by name
and never handles a number.

## Verification

`make check` — ruff, 91 backend tests, ESLint, TypeScript, production build.
`make counterfactual` — the second call is evidence-driven, not scripted.
`make reliability` — 5 consecutive flagship runs.
Docker images are written but **unbuilt**: no Docker daemon was available.
