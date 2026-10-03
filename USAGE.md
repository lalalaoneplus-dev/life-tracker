# life-tracker — usage

Private, local life OS. FastAPI + stdlib sqlite3 + a plain HTML/JS/CSS frontend. Runs only on
`127.0.0.1`; nothing is deployed. Two principals:

- **Agent** (skills/CLI): `Authorization: Bearer $LIFE_TRACKER_AGENT_TOKEN` → full write access.
- **Browser**: a signed **HttpOnly, SameSite=Lax** session cookie. Read + completion toggles only —
  it can never create or delete. The agent bearer is never sent to the browser.

## Config

`~/.config/life-tracker/env` (KEY=VALUE, already present):

```
LIFE_TRACKER_AGENT_TOKEN=...     # bearer for writes (server-side only, never exposed)
LIFE_TRACKER_SESSION_SECRET=...  # browser local-unlock secret + cookie signing key
LIFE_TRACKER_DB=...              # sqlite path, outside iCloud (created/migrated on startup)
```

Precedence: explicit `create_app(...)` args > environment variables > the env file. Secrets are
never hardcoded and never logged.

## Run

```bash
./run.sh                 # loads env, starts uvicorn on http://127.0.0.1:8787
```

Open http://127.0.0.1:8787/ and unlock with the session secret. Uvicorn is served via the app
factory (`app.main:create_app --factory`), so importing the package has no side effects.

## Test

```bash
PYTHONPATH=. ./.venv/bin/python -m pytest -q      # 31 tests: semantics + API + auth + concurrency
./smoke.sh                                         # live: boots a THROWAWAY temp DB, hits the API
```

`smoke.sh` never touches the real DB and never prints the bearer.

## Frontend views (mobile-first, single centered column)

1. **Today** — prev/today/next date; the day's due habits (expandable parent habits with checkable
   subtasks), goal next-actions, project next-actions; completion count; each item shows what it supports.
2. **Goals** — grouped by life area; outcome, definition-of-done, status, target/review dates,
   milestones, progress from success criteria + milestones. Creation/restructuring is agent-only.
3. **Projects** — status, phase, deadline, blockers, next action; tasks; health from explicit rules.
4. **Data** — **read-only** dashboard: goal/project/habit/metric summaries, a metric picker, one chart
   at a time, range 7/30/90/365, coverage counts. No inputs, no create/delete/correct controls.

## Data semantics (enforced in `app/semantics.py`, tested in `tests/`)

- Habit completion % is over **scheduled days only**; unscheduled days and paused habits are never failures.
- **Goal progress** = success criteria met + milestones completed only — never elapsed time or task counts.
- **Project progress** = explicit task/milestone scope only — never elapsed time.
- Each metric's missing-value meaning is distinct (`unknown`/`zero`/`not_applicable`/`not_scheduled`/`incomplete`);
  averages use valid observations only; `zero`/`incomplete` count missing days as 0, the others exclude them.
- Corrections/deletes are explicit and read-before-delete; completions are only ever set by explicit toggles.

## API quick reference

Reads (session or bearer): `GET /api/today?date=YYYY-MM-DD`, `/api/goals`, `/api/projects`,
`/api/metrics`, `/api/data/summary`, `/api/data/metric/{slug}?range=7|30|90|365`.
Toggle (session or bearer): `POST /api/complete {kind:habit|subtask|task, id, date, done}`.
Writes (bearer only): create/patch goals, criteria, milestones, projects, tasks, habits, subtasks,
metrics, observations (+ correct/delete), and links. Writes return the updated record/aggregate.
Auth failures: `401` (no principal), `403` (session attempting a write).
