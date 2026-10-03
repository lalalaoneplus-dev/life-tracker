# life-tracker — build contract

Build a **private, local** life operating system. Priority domain: **goals & habits** (build these richest;
projects and custom metrics are lighter but present). Lean stack, no over-engineering. Everything runs on this
Mac; nothing is deployed.

## Stack (use these; don't add heavy deps)
- Backend: **Python + FastAPI + uvicorn**, **stdlib sqlite3** for storage. One package (`app/`), not one giant file.
- Frontend: **plain HTML + vanilla JS + CSS**, served by FastAPI as static files. **No framework, no build step.**
- Tests: **pytest** + FastAPI `TestClient`.
- Python: system `python3` (3.13). Create `.venv`, `requirements.txt` (fastapi, uvicorn, pytest, httpx).

## Config / secrets (never hardcode; never expose the bearer to the browser)
Load from `~/.config/life-tracker/env` (already created, KEY=VALUE lines):
`LIFE_TRACKER_AGENT_TOKEN`, `LIFE_TRACKER_SESSION_SECRET`, `LIFE_TRACKER_DB`.
- DB path comes from `LIFE_TRACKER_DB` (a local path OUTSIDE iCloud — already set). Create it/migrate on startup.
- The **agent bearer** authorizes write endpoints. The **browser** uses a signed, **HttpOnly, SameSite=Lax** session cookie for completion toggles only. The bearer must NEVER appear in any HTML/JS served to the browser, in URLs, or in logs.

## Auth model (two principals)
- **Agent** (skills/CLI): `Authorization: Bearer <LIFE_TRACKER_AGENT_TOKEN>` → full write access (goals, projects, metrics, logs, corrections, deletes).
- **Browser session**: a local login sets the session cookie (single-user; the "login" can be a simple local unlock using the session secret — no passwords stored). Session can ONLY: read views, toggle habit/subtask/task completion. It CANNOT create goals/metrics or delete data.
- All other requests → 401. Validate every input (dates `YYYY-MM-DD`, types, ranges, unknown fields rejected, scheduled-habit constraints).

## Data model (SQLite) — minimum tables
- `goals`(id, slug, title, life_area, outcome, definition_of_done, status[not_started|active|blocked|paused|completed|abandoned], target_date, review_on, status_reason, created_at, updated_at)
- `success_criteria`(id, goal_id, text, met[0/1], measure)
- `milestones`(id, goal_id, title, status, order_idx, completed_at)
- `habits`(id, slug, title, schedule[daily|weekly|weekday_set|interval|min_frequency], schedule_detail, status[active|paused|completed|abandoned], supports_goal_id, created_at)
- `habit_subtasks`(id, habit_id, title, order_idx)
- `habit_completions`(id, habit_id, subtask_id NULL, occurred_on[date], created_at)  — completion only via explicit action
- `projects`(id, slug, title, status, phase, deadline, next_action, blockers, updated_at)
- `tasks`(id, project_id, title, done[0/1], order_idx, completed_at)
- `links`(id, from_type, from_id, to_type, to_id)  — habit↔goal, task↔project, project↔goal
- `metrics`(id, slug, label, value_type[numeric|boolean|categorical|duration|count|rating|text], unit, range_spec, aggregation[mean|sum|last|count|none], chart, privacy, missing_means[unknown|zero|not_applicable|not_scheduled|incomplete], created_at)
- `observations`(id, metric_id, occurred_on[date], value, note, estimated[0/1], created_at)  — dated life data
Store selected-date and created timestamp separately. Never rewrite history: corrections/deletes are explicit and read-before-delete.

## Data semantics (enforce in aggregation code + test them)
- Each metric declares its missing-value meaning; unknown / zero / not_applicable / not_scheduled / not_completed are DISTINCT.
- Averages use only valid observations. Intermittent metrics ignore missing days.
- **Habit completion % is computed only over SCHEDULED days** (paused/skipped days are not failures).
- **Goal progress** derives from success criteria met or milestones completed — NEVER from elapsed time or task counts alone.
- **Project progress** derives from explicit milestones/task scope — never fabricated from elapsed time.
- Do not imply correlation = causation anywhere.

## API (JSON; auth as above)
Reads (session or bearer): `GET /api/today?date=YYYY-MM-DD` (habits+subtasks due, goal next-actions, project next-actions, completion counts, what-supports-what), `GET /api/goals`, `GET /api/projects`, `GET /api/data/summary`, `GET /api/data/metric/{slug}?range=7|30|90|365`, `GET /api/metrics`.
Completion toggles (session or bearer): `POST /api/complete` {kind:habit|subtask|task, id, date, done:bool} (reopen supported).
Writes (bearer only): create/update goals, success_criteria, milestones, review dates, goal status; create/update projects, tasks, blockers, status; create/link habits + subtasks + schedules; define/update metrics; add/correct/delete observations (read id before delete); link habits/tasks/projects/goals; change targets/schedules/cadences.
Concurrency: guard so a browser toggle and an agent write don't lose each other (per-row updated_at check or a short transaction). Return the updated record/aggregate after writes.

## Frontend — 4 views (mobile-first, restrained, system fonts)
Single centered column. Readable without zoom. Touch targets ≥44×44. Subtle sync indicator; errors only when actionable; explicit loading/empty/offline/retry/all-complete states. Accessible names, visible focus, chart text alternatives. NO permanent calendar strip, NO 3-column boards, NO repeated summary cards, NO simultaneous charts, NO tiny uppercase labels.
1. **today** — prev/today/next date; one stable vertical list of the day's habits (expandable parent habits with individually checkable subtasks), due goal next-actions, project next-actions; completed items collapsed; completion count + progress; each item shows which goal/project it supports.
2. **goals** — active goals grouped by life area; outcome, definition-of-done, status, target date, next review; milestones + progress from success criteria; supporting projects/habits/next-actions; blocked/paused/completed/abandoned kept (not deleted). Web app allows only simple completion toggles; creation/restructuring is agent-only (show a hint).
3. **projects** — active projects: status, phase, deadline, blockers, next action; expandable milestones/tasks; health from explicit rules only; completed/archived retrievable.
4. **data** — READ-ONLY dashboard. **No log/metric/target/goal/note form inputs, no delete/correct controls** (agent is the write interface). Compact summaries (goal progress, project delivery, habit consistency, selected metrics); a metric picker from the registry; **one readable chart at a time** (type chosen by data semantics: line/bar/calendar/count/distribution/categorical-history); range 7/30/90/365; coverage counts (how many observations back each average); auto-refresh on focus + reasonable polling so agent writes appear.

## Tests (pytest — must pass)
schema/invalid-data rejection; checklist parent + nested-subtask completion & reopen; habit schedules/skip/pause and unscheduled-day semantics (not counted as failure); goal decomposition + milestone completion + review dates; project task/blocker/status transitions; links among goals/projects/habits/tasks; concurrent update without lost state; custom metric validation + aggregation; missing values per declared semantics; intermittent metrics excluding missing days; **goal progress NOT inferred from elapsed time or unrelated task counts**; empty & sparse data; unauthorized API (401 without bearer/session; session cannot write); data view returns no form-input affordances (assert the served data page has no create/delete controls). Add a live-smoke script (start uvicorn, hit `/api/today`, one toggle, one bearer write, read back).

## Deliverables
`app/` (FastAPI + sqlite + static frontend), `requirements.txt`, `tests/`, `run.sh` (loads env, starts uvicorn on 127.0.0.1:8787), `smoke.sh` (live checks), and a short `USAGE.md`. Update `README.md` if needed. Do NOT commit secrets or the DB. Keep it lean — the smallest code that satisfies this contract and passes the tests.
