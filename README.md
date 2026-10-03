# life-tracker

A local-first life operating system: today's checklist, goals & habits, projects, and a read-only
data dashboard. Runs **locally** (SQLite + localhost) — no hosting, no private data online.

- The **web UI** handles explicit completion toggles (session cookie).
- The **agent** is the write interface for goals, projects, metric definitions, logs, and corrections
  (separate bearer token, loaded from `~/.config/life-tracker/env`, never in source or the browser bundle).
- Pairs with an agent skill that reads and writes through the bearer-token API.

Secrets and the database live outside this repo: `~/.config/life-tracker/env`,
`~/Library/Application Support/life-tracker/tracker.db`. See `SPEC.md` for the full contract.
