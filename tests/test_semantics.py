"""Pure semantics tests — the enforced data rules, no HTTP."""
from datetime import date

import pytest

from app import semantics as S


def H(**kw):
    base = {"status": "active", "created_at": "2026-08-01", "schedule": "daily", "schedule_detail": None}
    base.update(kw)
    return base


# ---------- habit completion % over scheduled days only ----------

def test_daily_rate_over_scheduled_days():
    h = H(schedule="daily", created_at="2026-08-01")
    done = ["2026-08-01", "2026-08-03", "2026-08-05"]
    st = S.habit_stats(h, done, date(2026, 8, 1), date(2026, 8, 7))
    assert st["scheduled"] == 7 and st["completed"] == 3
    assert st["rate"] == pytest.approx(3 / 7, abs=1e-4)


def test_weekday_set_unscheduled_days_not_failures():
    # Mon-Fri only. 2026-08-03 is a Monday; weekend (08-08/09) must not count as misses.
    h = H(schedule="weekday_set", schedule_detail='{"weekdays":[0,1,2,3,4]}', created_at="2026-08-03")
    done = ["2026-08-03", "2026-08-04"]  # Mon, Tue done; Wed-Fri missed
    st = S.habit_stats(h, done, date(2026, 8, 3), date(2026, 8, 9))
    assert st["scheduled"] == 5  # Mon..Fri, weekend excluded
    assert st["completed"] == 2
    assert st["rate"] == pytest.approx(2 / 5, abs=1e-4)


def test_paused_habit_has_no_scheduled_days():
    h = H(schedule="daily", status="paused")
    st = S.habit_stats(h, [], date(2026, 8, 1), date(2026, 8, 7))
    assert st["scheduled"] == 0 and st["rate"] is None  # None, not 0% — pause is not failure


def test_interval_and_weekly_and_minfreq_slots():
    h = H(schedule="interval", schedule_detail='{"days":3}', created_at="2026-08-01")
    # slots on 08-01, 08-04, 08-07
    st = S.habit_stats(h, ["2026-08-01", "2026-08-07"], date(2026, 8, 1), date(2026, 8, 7))
    assert st["scheduled"] == 3 and st["completed"] == 2

    w = H(schedule="weekly", created_at="2026-08-03")  # Monday -> aligned to ISO weeks
    st = S.habit_stats(w, ["2026-08-03"], date(2026, 8, 3), date(2026, 8, 16))
    assert st["scheduled"] == 2 and st["completed"] == 1  # Mon08-03..Sun08-16 = 2 iso-weeks, 1 satisfied

    mf = H(schedule="min_frequency", schedule_detail='{"count":3,"period":"week"}', created_at="2026-08-03")
    # one iso-week (Mon 08-03..Sun 08-09), 2 completions of 3 required
    st = S.habit_stats(mf, ["2026-08-03", "2026-08-05"], date(2026, 8, 3), date(2026, 8, 9))
    assert st["scheduled"] == 3 and st["completed"] == 2


def test_due_on_semantics():
    daily = H(schedule="daily")
    assert S.due_on(daily, date(2026, 8, 5), []) is True
    assert S.due_on(daily, date(2026, 8, 5), ["2026-08-05"]) is False  # done -> not owed
    wd = H(schedule="weekday_set", schedule_detail='{"weekdays":[0,1,2,3,4]}')
    assert S.due_on(wd, date(2026, 8, 8), []) is False  # Saturday, unscheduled -> not owed
    assert S.due_on(wd, date(2026, 8, 7), []) is True   # Friday
    assert S.due_on(H(status="paused"), date(2026, 8, 5), []) is False


# ---------- goal / project progress independence ----------

def test_goal_progress_from_criteria_and_milestones_only():
    crit = [{"met": 1}, {"met": 0}]
    miles = [{"status": "completed"}, {"status": "active"}]
    assert S.goal_progress(crit, miles) == pytest.approx(0.5)
    assert S.goal_progress([], []) is None


def test_goal_progress_ignores_time_and_tasks():
    # No criteria met at all -> 0 regardless of how much time passed or tasks done elsewhere.
    crit = [{"met": 0}, {"met": 0}]
    assert S.goal_progress(crit, []) == 0.0
    # goal_progress takes no date and no task args at all — it structurally cannot use them.
    import inspect
    params = inspect.signature(S.goal_progress).parameters
    assert set(params) == {"criteria", "milestones"}


def test_project_progress_from_scope_not_time():
    tasks = [{"done": 1}, {"done": 0}, {"done": 0}, {"done": 0}]
    assert S.project_progress(tasks) == pytest.approx(0.25)
    assert S.project_progress([]) is None
    import inspect
    assert "deadline" not in inspect.signature(S.project_progress).parameters


# ---------- metric aggregation & missing-value semantics ----------

def M(**kw):
    base = {"value_type": "numeric", "aggregation": "mean", "missing_means": "unknown"}
    base.update(kw)
    return base


def obs(day, val):
    return {"occurred_on": day, "value": val, "created_at": day}


def test_mean_uses_valid_observations_only():
    o = [obs("2026-08-02", "4"), obs("2026-08-04", "6")]
    r = S.aggregate_metric(M(missing_means="unknown"), o, date(2026, 8, 1), date(2026, 8, 7))
    assert r["value"] == pytest.approx(5.0) and r["coverage"] == 2 and r["days_in_range"] == 7


def test_missing_zero_differs_from_unknown():
    o = [obs("2026-08-02", "4"), obs("2026-08-04", "6")]
    unknown = S.aggregate_metric(M(missing_means="unknown"), o, date(2026, 8, 1), date(2026, 8, 7))
    zero = S.aggregate_metric(M(missing_means="zero"), o, date(2026, 8, 1), date(2026, 8, 7))
    assert unknown["value"] == pytest.approx(5.0)
    assert zero["value"] == pytest.approx(10 / 7)  # missing days counted as 0
    assert unknown["value"] != zero["value"]


def test_boolean_incomplete_means_zero():
    o = [obs("2026-08-02", "true"), obs("2026-08-05", "true")]
    m = M(value_type="boolean", aggregation="mean", missing_means="incomplete")
    r = S.aggregate_metric(m, o, date(2026, 8, 1), date(2026, 8, 7))
    assert r["value"] == pytest.approx(2 / 7)  # 2 done of 7 days
    m2 = M(value_type="boolean", aggregation="mean", missing_means="unknown")
    r2 = S.aggregate_metric(m2, o, date(2026, 8, 1), date(2026, 8, 7))
    assert r2["value"] == pytest.approx(1.0)  # both observed days true


def test_not_applicable_and_not_scheduled_exclude_missing():
    o = [obs("2026-08-02", "4"), obs("2026-08-04", "6")]
    for mm in ("not_applicable", "not_scheduled"):
        r = S.aggregate_metric(M(missing_means=mm), o, date(2026, 8, 1), date(2026, 8, 7))
        assert r["value"] == pytest.approx(5.0) and r["coverage"] == 2


def test_categorical_distribution_and_last_and_empty():
    cat = M(value_type="categorical", aggregation="none")
    o = [obs("2026-08-01", "good"), obs("2026-08-02", "good"), obs("2026-08-03", "bad")]
    r = S.aggregate_metric(cat, o, date(2026, 8, 1), date(2026, 8, 7))
    assert r["distribution"] == {"good": 2, "bad": 1}

    last = M(aggregation="last")
    r = S.aggregate_metric(last, [obs("2026-08-01", "3"), obs("2026-08-05", "9")], date(2026, 8, 1), date(2026, 8, 7))
    assert r["value"] == pytest.approx(9.0)

    r = S.aggregate_metric(M(aggregation="mean"), [], date(2026, 8, 1), date(2026, 8, 7))
    assert r["value"] is None and r["coverage"] == 0


def test_parse_date_strict():
    assert S.parse_date("2026-08-11") == date(2026, 8, 11)
    for bad in ("2026-8-11", "08/11/2026", "not-a-date", "2026-13-01"):
        with pytest.raises(ValueError):
            S.parse_date(bad)
