"""Config loader. Precedence: explicit args > os.environ > ~/.config/life-tracker/env.

Secrets are never hardcoded and never logged. The agent bearer stays server-side only.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

ENV_FILE = Path.home() / ".config" / "life-tracker" / "env"
KEYS = ("LIFE_TRACKER_AGENT_TOKEN", "LIFE_TRACKER_SESSION_SECRET", "LIFE_TRACKER_DB")


def _read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


class Settings(NamedTuple):
    agent_token: str
    session_secret: str
    db_path: str


def load_settings(
    *, db_path: str | None = None, agent_token: str | None = None, session_secret: str | None = None
) -> Settings:
    filevals = _read_env_file()

    def pick(explicit: str | None, key: str) -> str:
        if explicit is not None:
            return explicit
        return os.environ.get(key) or filevals.get(key, "")

    token = pick(agent_token, "LIFE_TRACKER_AGENT_TOKEN")
    secret = pick(session_secret, "LIFE_TRACKER_SESSION_SECRET")
    db = pick(db_path, "LIFE_TRACKER_DB")
    missing = [k for k, val in zip(KEYS, (token, secret, db)) if not val]
    if missing:
        raise RuntimeError(f"missing config: {', '.join(missing)} (set in {ENV_FILE} or env vars)")
    return Settings(agent_token=token, session_secret=secret, db_path=db)
