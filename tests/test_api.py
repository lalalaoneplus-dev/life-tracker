"""Integration tests over the HTTP API (TestClient)."""
import re
import threading
from pathlib import Path


def mk_goal(agent, slug="g1", **kw):
    body = {"slug": slug, "title": "Ship thing", "life_area": "career", "status": "active"}
    body.update(kw)
    r = agent.post("/api/goals", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def mk_habit(agent, slug="h1", schedule="daily", detail=None, **kw):
    body = {"slug": slug, "title": "Read", "schedule": schedule}
    if detail is not None:
        body["schedule_detail"] = detail
    body.update(kw)
    r = agent.post("/api/habits", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def mk_project(agent, slug="p1", **kw):
    body = {"slug": slug, "title": "Website", "status": "active"}
    body.update(kw)
    r = agent.post("/api/projects", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------- auth ----------------

def test_anon_cannot_read_or_write(anon):
    assert anon.get("/api/today").status_code == 401
    assert anon.post("/api/goals", json={"slug": "x", "title": "y"}).status_code == 401


def test_wrong_bearer_is_401(app):
    from fastapi.testclient import TestClient
    c = TestClient(app, headers={"Authorization": "Bearer NOPE"})
    assert c.get("/api/today").status_code == 401


def test_session_can_read_but_not_write(session):
    assert session.get("/api/today").status_code == 200
    r = session.post("/api/goals", json={"slug": "x", "title": "y"})
    assert r.status_code == 403  # authenticated session, but writes need the agent bearer


def test_session_can_toggle_completion(agent, session):
    h = mk_habit(agent)
    on = h["created_at"][:10]
    r = session.post("/api/complete", json={"kind": "habit", "id": h["id"], "date": on, "done": True})
    assert r.status_code == 200 and r.json()["done_today"] is True


# ---------------- input validation ----------------

def test_invalid_goal_payloads_rejected(agent):
    assert agent.post("/api/goals", json={"slug": "a", "title": "t", "status": "bogus"}).status_code == 422
    assert agent.post("/api/goals", json={"slug": "a", "title": "t", "surprise": 1}).status_code == 422
    assert agent.post("/api/goals", json={"slug": "a", "title": "t", "target_date": "2026-13-40"}).status_code == 422
    assert agent.post("/api/goals", json={"title": "no slug"}).status_code == 422


def test_habit_schedule_detail_validation(agent):
    assert agent.post("/api/habits", json={"slug": "w", "title": "t", "schedule": "weekday_set"}).status_code == 400
    assert agent.post("/api/habits", json={"slug": "w", "title": "t", "schedule": "weekday_set",
                                           "schedule_detail": {"weekdays": [9]}}).status_code == 400
    assert agent.post("/api/habits", json={"slug": "w", "title": "t", "schedule": "interval",
                                           "schedule_detail": {"days": 0}}).status_code == 400
    assert agent.post("/api/habits", json={"slug": "w", "title": "t", "schedule": "daily"}).status_code == 201


# ---------------- goals: decomposition, milestones, reviews ----------------

def test_goal_decomposition_and_milestone_completion(agent):
    g = mk_goal(agent)
    gid = g["id"]
    agent.post(f"/api/goals/{gid}/criteria", json={"text": "A", "met": False})
    agent.post(f"/api/goals/{gid}/criteria", json={"text": "B", "met": True})
    agent.post(f"/api/goals/{gid}/milestones", json={"title": "M1", "order_idx": 1})
    r = agent.post(f"/api/goals/{gid}/milestones", json={"title": "M2", "order_idx": 2})
    body = r.json()
    assert body["progress"] == 0.25  # 1 of (2 criteria + 2 milestones)
    mid = [m for m in body["milestones"] if m["title"] == "M1"][0]["id"]
    r = agent.patch(f"/api/milestones/{mid}", json={"status": "completed"})
    m1 = [m for m in r.json()["milestones"] if m["id"] == mid][0]
    assert m1["completed_at"] is not None and r.json()["progress"] == 0.5
    # review date
    r = agent.patch(f"/api/goals/{gid}", json={"review_on": "2026-09-01"})
    assert r.json()["review_on"] == "2026-09-01"


def test_goal_progress_not_from_time_or_task_counts(agent):
    # target date far in the past, a fully-delivered linked project, but zero criteria met.
    g = mk_goal(agent, slug="gp", target_date="2020-01-01")
    gid = g["id"]
    agent.post(f"/api/goals/{gid}/criteria", json={"text": "must ship", "met": False})
    p = mk_project(agent, slug="pp")
    r = agent.post(f"/api/projects/{p['id']}/tasks", json={"title": "done task"})
    tid = r.json()["tasks"][0]["id"]
    agent.patch(f"/api/tasks/{tid}", json={"done": True})  # 100% project delivery
    agent.post("/api/links", json={"from_type": "project", "from_id": p["id"], "to_type": "goal", "to_id": gid})
    goals = agent.get("/api/goals").json()["goals"]
    goal = [x for x in goals if x["id"] == gid][0]
    assert goal["progress"] == 0.0  # unaffected by elapsed time or the delivered project's tasks


# ---------------- projects ----------------

def test_project_tasks_blockers_status_and_health(agent, session):
    p = mk_project(agent, next_action="draft copy", deadline="2020-01-01")
    pid = p["id"]
    r = agent.post(f"/api/projects/{pid}/tasks", json={"title": "T1"})
    r = agent.post(f"/api/projects/{pid}/tasks", json={"title": "T2"})
    tasks = r.json()["tasks"]
    assert r.json()["progress"] == 0.0 and r.json()["health"] == "at_risk"  # deadline passed, nothing done
    # session toggles a task
    on = "2026-08-11"
    session.post("/api/complete", json={"kind": "task", "id": tasks[0]["id"], "date": on, "done": True})
    p2 = agent.get("/api/projects").json()["projects"][0]
    assert p2["progress"] == 0.5
    # blockers make it blocked regardless of dates
    r = agent.patch(f"/api/projects/{pid}", json={"blockers": "waiting on API key"})
    assert r.json()["health"] == "blocked"
    r = agent.patch(f"/api/projects/{pid}", json={"status": "completed"})
    assert r.json()["health"] == "done"


def test_links_between_goal_and_project(agent):
    g = mk_goal(agent)
    p = mk_project(agent)
    r = agent.post("/api/links", json={"from_type": "project", "from_id": p["id"], "to_type": "goal", "to_id": g["id"]})
    lid = r.json()["id"]
    goal = agent.get("/api/goals").json()["goals"][0]
    assert any(sp["id"] == p["id"] for sp in goal["supporting_projects"])
    proj = agent.get("/api/projects").json()["projects"][0]
    assert any(lg["id"] == g["id"] for lg in proj["linked_goals"])
    assert agent.delete(f"/api/links/{lid}").status_code == 200
    assert agent.post("/api/links", json={"from_type": "project", "from_id": p["id"],
                                          "to_type": "goal", "to_id": g["id"]}).status_code == 201  # re-link ok


# ---------------- habit checklist: parent + nested subtasks, reopen ----------------

def test_checklist_parent_and_nested_subtasks(agent, session):
    h = mk_habit(agent, slug="routine")
    hid = h["id"]
    s1 = agent.post(f"/api/habits/{hid}/subtasks", json={"title": "S1"}).json()["subtasks"][0]["id"]
    s2 = agent.post(f"/api/habits/{hid}/subtasks", json={"title": "S2"}).json()["subtasks"][-1]["id"]
    on = h["created_at"][:10]
    # complete one subtask -> parent not done
    r = session.post("/api/complete", json={"kind": "subtask", "id": s1, "date": on, "done": True})
    assert r.json()["done_today"] is False
    # complete both -> parent done
    r = session.post("/api/complete", json={"kind": "subtask", "id": s2, "date": on, "done": True})
    assert r.json()["done_today"] is True
    # reopen one -> parent not done again
    r = session.post("/api/complete", json={"kind": "subtask", "id": s1, "date": on, "done": False})
    assert r.json()["done_today"] is False


def test_habit_schedule_pause_and_unscheduled_in_today(agent):
    h = mk_habit(agent, slug="daily1", schedule="daily")
    on = h["created_at"][:10]
    t = agent.get(f"/api/today?date={on}").json()
    assert any(x["id"] == h["id"] and x["pending"] for x in t["habits"])
    # pause -> disappears from today (not a pending failure)
    agent.patch(f"/api/habits/{h['id']}", json={"status": "paused"})
    t = agent.get(f"/api/today?date={on}").json()
    assert all(x["id"] != h["id"] for x in t["habits"])

    # weekday_set: unscheduled weekday is absent (not a miss)
    from datetime import date
    wd = date.fromisoformat(on).weekday()
    other = (wd + 1) % 7
    hw = mk_habit(agent, slug="wd", schedule="weekday_set", detail={"weekdays": [other]})
    t = agent.get(f"/api/today?date={on}").json()
    assert all(x["id"] != hw["id"] for x in t["habits"])  # not scheduled that day


# ---------------- metrics ----------------

def test_metric_validation_and_aggregation(agent):
    assert agent.post("/api/metrics", json={"slug": "m", "label": "L", "value_type": "bogus"}).status_code == 422
    r = agent.post("/api/metrics", json={"slug": "mood", "label": "Mood", "value_type": "rating",
                                         "aggregation": "mean", "range_spec": "1-5", "missing_means": "unknown"})
    assert r.status_code == 201
    # out-of-range and wrong-type observations rejected
    assert agent.post("/api/metrics/mood/observations", json={"occurred_on": "2026-08-01", "value": "9"}).status_code == 400
    assert agent.post("/api/metrics/mood/observations", json={"occurred_on": "2026-08-01", "value": "x"}).status_code == 400
    for day, val in (("2026-08-10", "4"), ("2026-08-11", "2")):
        assert agent.post("/api/metrics/mood/observations", json={"occurred_on": day, "value": val}).status_code == 201
    d = agent.get("/api/data/metric/mood?range=365").json()
    assert d["aggregate"]["aggregation"] == "mean" and d["coverage"] == 2
    assert d["aggregate"]["value"] == 3.0 and d["chart_type"] == "line"
    assert agent.get("/api/data/metric/mood?range=5").status_code == 400  # bad range


def test_observation_correct_and_delete_reads_before_delete(agent):
    agent.post("/api/metrics", json={"slug": "wt", "label": "Weight", "value_type": "numeric", "aggregation": "last"})
    o = agent.post("/api/metrics/wt/observations", json={"occurred_on": "2026-08-11", "value": "80"}).json()
    r = agent.patch(f"/api/observations/{o['id']}", json={"value": "81"})
    assert r.json()["value"] == "81"
    r = agent.delete(f"/api/observations/{o['id']}")
    assert r.status_code == 200 and r.json()["deleted"]["id"] == o["id"]
    assert agent.delete(f"/api/observations/{o['id']}").status_code == 404


# ---------------- empty / sparse ----------------

def test_empty_database_endpoints(session):
    assert session.get("/api/today").json()["habits"] == []
    assert session.get("/api/goals").json()["goals"] == []
    assert session.get("/api/projects").json()["projects"] == []
    s = session.get("/api/data/summary").json()
    assert s["goals"] == [] and s["habits"] == [] and s["metrics"] == []


# ---------------- concurrency: toggle + agent write don't clobber ----------------

def test_concurrent_toggle_and_write_both_persist(agent, session):
    g = mk_goal(agent, slug="cg")
    h = mk_habit(agent, slug="ch")
    on = h["created_at"][:10]
    barrier = threading.Barrier(2)
    errs = []

    def toggle():
        try:
            barrier.wait()
            r = session.post("/api/complete", json={"kind": "habit", "id": h["id"], "date": on, "done": True})
            assert r.status_code == 200
        except Exception as e:  # noqa
            errs.append(e)

    def write():
        try:
            barrier.wait()
            r = agent.patch(f"/api/goals/{g['id']}", json={"status": "completed", "status_reason": "done"})
            assert r.status_code == 200
        except Exception as e:  # noqa
            errs.append(e)

    ts = [threading.Thread(target=toggle), threading.Thread(target=write)]
    [t.start() for t in ts]
    [t.join(timeout=5) for t in ts]
    assert not errs, errs
    # both effects present, neither lost
    goal = agent.get("/api/goals").json()["goals"][0]
    assert goal["status"] == "completed"
    t = agent.get(f"/api/today?date={on}").json()
    assert any(x["id"] == h["id"] and x["done_today"] for x in t["habits"])


# ---------------- data view is read-only (no write affordances) ----------------

def test_data_view_has_no_form_inputs():
    html = (Path(__file__).resolve().parent.parent / "app" / "static" / "index.html").read_text()
    assert "<input" in html.lower()  # the login unlock exists -> proves the slice below is scoped
    m = re.search(r'<section id="view-data".*?</section>', html, re.S)
    assert m, "view-data section not found"
    sec = m.group(0).lower()
    for tag in ("<input", "<textarea", "<form", "<select", "contenteditable"):
        assert tag not in sec, f"data view must not contain {tag}"
    for label in re.findall(r"<button[^>]*>(.*?)</button>", sec, re.S):
        assert not any(w in label for w in
                       ("add", "create", "delete", "save", "log", "correct", "remove", "new", "edit", "submit")), label
