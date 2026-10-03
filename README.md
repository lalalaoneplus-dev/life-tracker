# life-tracker

A local-first life operating system: today's checklist, goals & habits, projects, and a read-only
data dashboard. Runs **locally** (SQLite + localhost) — no hosting, no private data online.

- The **web UI** handles explicit completion toggles (session cookie).
- The **agent** is the write interface for goals, projects, metric definitions, logs, and corrections
  (separate bearer token, loaded from `~/.config/life-tracker/env`, never in source or the browser bundle).
- Pairs with an agent skill that reads and writes through the bearer-token API.

## Install

macOS:

```sh
curl -fsSL https://raw.githubusercontent.com/lalalaoneplus-dev/life-tracker/main/install.sh | bash
```

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/lalalaoneplus-dev/life-tracker/main/install.ps1 | iex
```

Sets up Python, local config, and the SQLite database, then opens http://127.0.0.1:8787.

Secrets live in `~/.config/life-tracker/env` (`%USERPROFILE%\.config\life-tracker\env` on Windows).
The database is `~/Library/Application Support/life-tracker/tracker.db` on macOS and
`%LOCALAPPDATA%\life-tracker\tracker.db` on Windows. See `SPEC.md` for the full contract.
