"""Pure aggregation semantics. No I/O — takes plain dicts/values, returns numbers.

Enforced (and tested) rules:
- Habit completion % is over SCHEDULED days only; unscheduled days and paused
  habits are NOT failures.
- Goal progress derives from success criteria + milestones only — never elapsed
  time or task counts.
- Project progress derives from explicit task/milestone scope — never elapsed time.
- Each metric's missing-value meaning is distinct; averages use valid observations
  only; 'zero'/'incomplete' fill missing days as 0, others are excluded.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Iterable

# ---------- dates ----------

def parse_date(s: str) -> date:
    """Strict YYYY-MM-DD. Raises ValueError on anything else."""
    if not isinstance(s, str) or len(s) != 10 or s[4] != "-" or s[7] != "-":
        raise ValueError(f"date must be YYYY-MM-DD, got {s!r}")
    return date.fromisoformat(s)


def daterange(start: date, end: date) -> Iterable[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _period_key(d: date, period: str) -> str:
    if period == "month":
        return f"{d.year}-{d.month:02d}"
    return _iso_week(d)  # default weekly


# ---------- habits ----------

def scheduled_slots(habit: dict, start: date, end: date) -> list[str]:
    """Slot keys the habit was expected on within [start, end].

    A slot is a date iso-string (day schedules) or a period key (weekly /
    min_frequency). Paused/completed/abandoned habits have NO scheduled slots,
    so their missing days can never count as failures.
    """
    if habit.get("status") != "active":
        return []
    created = parse_date(str(habit["created_at"])[:10])
    lo = max(start, created)
    if lo > end:
        return []
    sched = habit["schedule"]
    detail = json.loads(habit.get("schedule_detail") or "{}")

    if sched == "daily":
        return [d.isoformat() for d in daterange(lo, end)]

    if sched == "weekday_set":
        wd = set(detail.get("weekdays", []))  # 0=Mon .. 6=Sun (date.weekday())
        return [d.isoformat() for d in daterange(lo, end) if d.weekday() in wd]

    if sched == "interval":
        n = max(1, int(detail.get("days", 1)))
        k0 = max(0, -(-(lo - created).days // n))  # ceil
        slots, d = [], created + timedelta(days=k0 * n)
        while d <= end:
            slots.append(d.isoformat())
            d += timedelta(days=n)
        return slots

    if sched == "weekly":
        seen, out = set(), []
        for d in daterange(lo, end):
            k = _iso_week(d)
            if k not in seen:
                seen.add(k)
                out.append(k)
        return out

    if sched == "min_frequency":
        count = max(1, int(detail.get("count", 1)))
        period = detail.get("period", "week")
        seen, periods = set(), []
        for d in daterange(lo, end):
            k = _period_key(d, period)
            if k not in seen:
                seen.add(k)
                periods.append(k)
        return [f"{p}#{i}" for p in periods for i in range(count)]
    raise ValueError(f"unknown schedule {sched!r}")


def due_on(habit: dict, on: date, completion_dates: Iterable[str]) -> bool:
    """Is the habit OWED (scheduled and not yet satisfied) on `on`?

    Unscheduled days return False (not a failure, just nothing owed). Paused/etc.
    habits are never owed.
    """
    if habit.get("status") != "active":
        return False
    created = parse_date(str(habit["created_at"])[:10])
    if on < created:
        return False
    sched = habit["schedule"]
    detail = json.loads(habit.get("schedule_detail") or "{}")
    done = {c[:10] for c in completion_dates}
    on_iso = on.isoformat()

    if sched == "daily":
        return on_iso not in done
    if sched == "weekday_set":
        return on.weekday() in set(detail.get("weekdays", [])) and on_iso not in done
    if sched == "interval":
        n = max(1, int(detail.get("days", 1)))
        past = sorted(d for d in done if date.fromisoformat(d) <= on)
        if not past:
            return (on - created).days % n == 0
        return (on - date.fromisoformat(past[-1])).days >= n
    if sched == "weekly":
        wk = _iso_week(on)
        return not any(_iso_week(date.fromisoformat(d)) == wk for d in done)
    if sched == "min_frequency":
        count = max(1, int(detail.get("count", 1)))
        period = detail.get("period", "week")
        pk = _period_key(on, period)
        got = sum(1 for d in done if _period_key(date.fromisoformat(d), period) == pk)
        return got < count
    return False


def habit_stats(habit: dict, completion_dates: Iterable[str], start: date, end: date) -> dict:
    """completion_dates: iso date strings of parent-habit completions in range.

    Returns scheduled/completed slot counts and a rate over scheduled days only
    (None when nothing was scheduled — distinct from 0%).
    """
    slots = scheduled_slots(habit, start, end)
    scheduled = len(slots)
    done_days = {c[:10] for c in completion_dates}

    sched = habit["schedule"]
    if sched in ("daily", "weekday_set", "interval"):
        completed = sum(1 for s in slots if s in done_days)
    elif sched == "weekly":
        done_weeks = {_iso_week(date.fromisoformat(d)) for d in done_days}
        completed = sum(1 for s in slots if s in done_weeks)
    elif sched == "min_frequency":
        detail = json.loads(habit.get("schedule_detail") or "{}")
        period = detail.get("period", "week")
        per_period: dict[str, int] = {}
        for d in done_days:
            k = _period_key(date.fromisoformat(d), period)
            per_period[k] = per_period.get(k, 0) + 1
        # each scheduled slot is one required completion in its period
        need: dict[str, int] = {}
        for s in slots:
            need[s.split("#")[0]] = need.get(s.split("#")[0], 0) + 1
        completed = sum(min(per_period.get(p, 0), n) for p, n in need.items())
    else:
        completed = 0

    rate = None if scheduled == 0 else round(completed / scheduled, 4)
    return {"scheduled": scheduled, "completed": completed, "rate": rate}


# ---------- goals & projects ----------

def goal_progress(criteria: list[dict], milestones: list[dict]) -> float | None:
    """From success criteria met + milestones completed ONLY. None if neither exists.

    Explicitly independent of target_date/elapsed time and of task counts.
    """
    total = len(criteria) + len(milestones)
    if total == 0:
        return None
    met = sum(1 for c in criteria if c.get("met")) + sum(
        1 for m in milestones if m.get("status") == "completed"
    )
    return round(met / total, 4)


def project_progress(tasks: list[dict], milestones: list[dict] | None = None) -> float | None:
    """From explicit task/milestone scope only. None if no scope. Never time-based."""
    ms = milestones or []
    total = len(tasks) + len(ms)
    if total == 0:
        return None
    done = sum(1 for t in tasks if t.get("done")) + sum(
        1 for m in ms if m.get("status") == "completed"
    )
    return round(done / total, 4)


# ---------- metrics ----------

_NUMERIC_TYPES = {"numeric", "duration", "count", "rating"}
_EXCLUDE_MISSING = {"unknown", "not_applicable", "not_scheduled"}  # missing -> excluded
_ZERO_MISSING = {"zero", "incomplete"}  # missing day -> counts as 0


def coerce_value(value_type: str, raw):
    """Parse a stored observation value per its metric type. Raises ValueError."""
    if raw is None or raw == "":
        raise ValueError("empty value")
    if value_type in _NUMERIC_TYPES:
        return float(raw)
    if value_type == "boolean":
        s = str(raw).strip().lower()
        if s in ("1", "true", "yes", "y", "t"):
            return 1.0
        if s in ("0", "false", "no", "n", "f"):
            return 0.0
        raise ValueError(f"bad boolean {raw!r}")
    return str(raw)  # categorical / text


def aggregate_metric(metric: dict, observations: list[dict], start: date, end: date) -> dict:
    """Aggregate observations in [start,end] per the metric's declared semantics.

    Only valid observations count toward averages (missing days are ignored unless
    missing_means is zero/incomplete, in which case each missing day in range is 0).
    Returns value/coverage and, for categorical, a distribution.
    """
    vt = metric["value_type"]
    agg = metric.get("aggregation", "none")
    missing = metric.get("missing_means", "unknown")

    obs = [o for o in observations if start <= parse_date(o["occurred_on"]) <= end]

    if vt == "categorical":
        dist: dict[str, int] = {}
        for o in obs:
            k = str(o["value"])
            dist[k] = dist.get(k, 0) + 1
        return {"aggregation": "distribution", "distribution": dist, "coverage": len(obs),
                "days_in_range": (end - start).days + 1}

    values, by_day = [], {}
    for o in obs:
        try:
            v = coerce_value(vt, o["value"])
        except ValueError:
            continue
        values.append(v)
        by_day[o["occurred_on"]] = v

    coverage = len(values)  # valid observations backing the aggregate
    days = (end - start).days + 1

    out = {"aggregation": agg, "coverage": coverage, "days_in_range": days, "value": None}
    if agg == "none":
        return out
    if agg == "last":
        out["value"] = None if not obs else coerce_last(vt, obs)
        return out
    if agg == "count":
        out["value"] = coverage
        return out
    if not values and missing not in _ZERO_MISSING:
        return out  # nothing to average/sum

    if missing in _ZERO_MISSING:
        # every day in range contributes; missing days are 0
        total = sum(by_day.get(d.isoformat(), 0.0) for d in daterange(start, end))
        n = days
    else:  # unknown / not_applicable / not_scheduled -> valid observations only
        total = sum(values)
        n = coverage

    if agg == "sum":
        out["value"] = round(total, 6)
    elif agg == "mean":
        out["value"] = None if n == 0 else round(total / n, 6)
    return out


def coerce_last(value_type: str, obs: list[dict]):
    latest = max(obs, key=lambda o: (o["occurred_on"], o.get("created_at", "")))
    try:
        return coerce_value(value_type, latest["value"])
    except ValueError:
        return latest["value"]
