"""SQLite storage: one connection + a write lock. Schema created on startup.

Concurrency: a single RLock serialises writes so a browser toggle and an agent
write never clobber each other (each write is one statement or a short IMMEDIATE
transaction). Optimistic checks use each row's updated_at where relevant.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS goals(
  id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
  life_area TEXT, outcome TEXT, definition_of_done TEXT,
  status TEXT NOT NULL DEFAULT 'not_started',
  target_date TEXT, review_on TEXT, status_reason TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS success_criteria(
  id INTEGER PRIMARY KEY, goal_id INTEGER NOT NULL REFERENCES goals(id),
  text TEXT NOT NULL, met INTEGER NOT NULL DEFAULT 0, measure TEXT);

CREATE TABLE IF NOT EXISTS milestones(
  id INTEGER PRIMARY KEY, goal_id INTEGER NOT NULL REFERENCES goals(id),
  title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'not_started',
  order_idx INTEGER NOT NULL DEFAULT 0, completed_at TEXT);

CREATE TABLE IF NOT EXISTS habits(
  id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
  schedule TEXT NOT NULL, schedule_detail TEXT,
  status TEXT NOT NULL DEFAULT 'active', supports_goal_id INTEGER REFERENCES goals(id),
  created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS habit_subtasks(
  id INTEGER PRIMARY KEY, habit_id INTEGER NOT NULL REFERENCES habits(id),
  title TEXT NOT NULL, order_idx INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS habit_completions(
  id INTEGER PRIMARY KEY, habit_id INTEGER NOT NULL REFERENCES habits(id),
  subtask_id INTEGER REFERENCES habit_subtasks(id),
  occurred_on TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(habit_id, subtask_id, occurred_on));

CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active', phase TEXT, deadline TEXT,
  next_action TEXT, blockers TEXT, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
  title TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
  order_idx INTEGER NOT NULL DEFAULT 0, completed_at TEXT);

CREATE TABLE IF NOT EXISTS links(
  id INTEGER PRIMARY KEY, from_type TEXT NOT NULL, from_id INTEGER NOT NULL,
  to_type TEXT NOT NULL, to_id INTEGER NOT NULL,
  UNIQUE(from_type, from_id, to_type, to_id));

CREATE TABLE IF NOT EXISTS metrics(
  id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, label TEXT NOT NULL,
  value_type TEXT NOT NULL, unit TEXT, range_spec TEXT,
  aggregation TEXT NOT NULL DEFAULT 'none', chart TEXT, privacy TEXT,
  missing_means TEXT NOT NULL DEFAULT 'unknown', created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS observations(
  id INTEGER PRIMARY KEY, metric_id INTEGER NOT NULL REFERENCES metrics(id),
  occurred_on TEXT NOT NULL, value TEXT, note TEXT,
  estimated INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
"""


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=4000")
        self.lock = threading.RLock()
        self.conn.executescript(SCHEMA)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def write(self, sql: str, params: tuple = ()) -> int:
        """Single write statement under the lock. Returns lastrowid."""
        with self.lock:
            cur = self.conn.execute(sql, params)
            return cur.lastrowid

    def close(self) -> None:
        self.conn.close()
