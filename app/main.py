"""FastAPI app: reads (session|agent), completion toggles (session|agent), writes (agent only).

Everything runs locally on 127.0.0.1. The agent bearer never reaches the browser.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from . import auth, semantics
from .config import load_settings
from .db import Database

STATIC = Path(__file__).parent / "static"


def _vdate(v):
    if v is None:
        return v
    semantics.parse_date(v)  # raises ValueError -> 422
    return v


DateStr = Annotated[str, BeforeValidator(_vdate)]

GoalStatus = Literal["not_started", "active", "blocked", "paused", "completed", "abandoned"]
MilestoneStatus = Literal["not_started", "active", "completed"]
HabitSchedule = Literal["daily", "weekly", "weekday_set", "interval", "min_frequency"]
HabitStatus = Literal["active", "paused", "completed", "abandoned"]
ProjectStatus = Literal["active", "paused", "completed", "blocked", "archived"]
ValueType = Literal["numeric", "boolean", "categorical", "duration", "count", "rating", "text"]
Aggregation = Literal["mean", "sum", "last", "count", "none"]
MissingMeans = Literal["unknown", "zero", "not_applicable", "not_scheduled", "incomplete"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_str() -> str:
    return date.today().isoformat()


# ---------------- request models (extra='forbid' rejects unknown fields) ----------------

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoalIn(Strict):
    slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    life_area: Optional[str] = None
    outcome: Optional[str] = None
    definition_of_done: Optional[str] = None
    status: GoalStatus = "not_started"
    target_date: Optional[DateStr] = None
    review_on: Optional[DateStr] = None
    status_reason: Optional[str] = None


class GoalPatch(Strict):
    title: Optional[str] = None
    life_area: Optional[str] = None
    outcome: Optional[str] = None
    definition_of_done: Optional[str] = None
    status: Optional[GoalStatus] = None
    target_date: Optional[DateStr] = None
    review_on: Optional[DateStr] = None
    status_reason: Optional[str] = None


class CriterionIn(Strict):
    text: str = Field(min_length=1)
    met: bool = False
    measure: Optional[str] = None


class CriterionPatch(Strict):
    text: Optional[str] = None
    met: Optional[bool] = None
    measure: Optional[str] = None


class MilestoneIn(Strict):
    title: str = Field(min_length=1)
    status: MilestoneStatus = "not_started"
    order_idx: int = 0


class MilestonePatch(Strict):
    title: Optional[str] = None
    status: Optional[MilestoneStatus] = None
    order_idx: Optional[int] = None


class HabitIn(Strict):
    slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    schedule: HabitSchedule
    schedule_detail: Optional[dict] = None
    status: HabitStatus = "active"
    supports_goal_id: Optional[int] = None


class HabitPatch(Strict):
    title: Optional[str] = None
    schedule: Optional[HabitSchedule] = None
    schedule_detail: Optional[dict] = None
    status: Optional[HabitStatus] = None
    supports_goal_id: Optional[int] = None


class SubtaskIn(Strict):
    title: str = Field(min_length=1)
    order_idx: int = 0


class ProjectIn(Strict):
    slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    status: ProjectStatus = "active"
    phase: Optional[str] = None
    deadline: Optional[DateStr] = None
    next_action: Optional[str] = None
    blockers: Optional[str] = None


class ProjectPatch(Strict):
    title: Optional[str] = None
    status: Optional[ProjectStatus] = None
    phase: Optional[str] = None
    deadline: Optional[DateStr] = None
    next_action: Optional[str] = None
    blockers: Optional[str] = None


class TaskIn(Strict):
    title: str = Field(min_length=1)
    order_idx: int = 0


class TaskPatch(Strict):
    title: Optional[str] = None
    done: Optional[bool] = None
    order_idx: Optional[int] = None


class MetricIn(Strict):
    slug: str = Field(min_length=1)
    label: str = Field(min_length=1)
    value_type: ValueType
    unit: Optional[str] = None
    range_spec: Optional[str] = None
    aggregation: Aggregation = "none"
    chart: Optional[str] = None
    privacy: Optional[str] = None
    missing_means: MissingMeans = "unknown"


class MetricPatch(Strict):
    label: Optional[str] = None
    unit: Optional[str] = None
    range_spec: Optional[str] = None
    aggregation: Optional[Aggregation] = None
    chart: Optional[str] = None
    privacy: Optional[str] = None
    missing_means: Optional[MissingMeans] = None


class ObservationIn(Strict):
    occurred_on: DateStr
    value: str
    note: Optional[str] = None
    estimated: bool = False


class ObservationPatch(Strict):
    occurred_on: Optional[DateStr] = None
    value: Optional[str] = None
    note: Optional[str] = None
    estimated: Optional[bool] = None


class LinkIn(Strict):
    from_type: Literal["habit", "task", "project", "goal"]
    from_id: int
    to_type: Literal["habit", "task", "project", "goal"]
    to_id: int


class CompleteIn(Strict):
    kind: Literal["habit", "subtask", "task"]
    id: int
    date: DateStr
    done: bool


class LoginIn(Strict):
    secret: str


# ---------------- app factory ----------------

def create_app(**overrides) -> FastAPI:
    settings = load_settings(**overrides)
    app = FastAPI(title="life-tracker", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.db = Database(settings.db_path)

    reader = Depends(auth.require_reader)
    agent = Depends(auth.require_agent)

    def db() -> Database:
        return app.state.db

    # ---- helpers ----
    def schedule_detail_validate(schedule: str, detail: Optional[dict]) -> str:
        detail = detail or {}
        if schedule == "weekday_set":
            wd = detail.get("weekdays")
            if not isinstance(wd, list) or not wd or any(not isinstance(x, int) or x < 0 or x > 6 for x in wd):
                raise HTTPException(400, "weekday_set needs weekdays: list of 0-6")
        elif schedule == "interval":
            if int(detail.get("days", 0)) < 1:
                raise HTTPException(400, "interval needs days >= 1")
        elif schedule == "min_frequency":
            if int(detail.get("count", 0)) < 1 or detail.get("period", "week") not in ("week", "month"):
                raise HTTPException(400, "min_frequency needs count >= 1 and period week|month")
        return json.dumps(detail)

    def obs_value_validate(metric: dict, value: str) -> None:
        try:
            v = semantics.coerce_value(metric["value_type"], value)
        except ValueError as e:
            raise HTTPException(400, f"value invalid for {metric['value_type']}: {e}")
        spec = metric.get("range_spec")
        if spec and metric["value_type"] in ("numeric", "duration", "count", "rating"):
            lo = hi = None
            try:
                j = json.loads(spec)
                lo, hi = j.get("min"), j.get("max")
            except (ValueError, AttributeError):
                if "-" in spec:
                    a, b = spec.split("-", 1)
                    lo, hi = float(a), float(b)
            if lo is not None and v < lo or hi is not None and v > hi:
                raise HTTPException(400, f"value out of range {spec}")

    def goal_full(gid: int) -> dict:
        g = db().one("SELECT * FROM goals WHERE id=?", (gid,))
        if not g:
            raise HTTPException(404, "goal not found")
        g = dict(g)
        crit = [dict(r) for r in db().query("SELECT * FROM success_criteria WHERE goal_id=?", (gid,))]
        miles = [dict(r) for r in db().query(
            "SELECT * FROM milestones WHERE goal_id=? ORDER BY order_idx, id", (gid,))]
        habits = [dict(r) for r in db().query(
            "SELECT id,slug,title,status FROM habits WHERE supports_goal_id=?", (gid,))]
        proj_ids = [r["from_id"] for r in db().query(
            "SELECT from_id FROM links WHERE from_type='project' AND to_type='goal' AND to_id=?", (gid,))]
        projects = [dict(db().one("SELECT id,slug,title,status FROM projects WHERE id=?", (pid,)))
                    for pid in proj_ids if db().one("SELECT 1 FROM projects WHERE id=?", (pid,))]
        next_ms = next((m["title"] for m in miles if m["status"] != "completed"), None)
        g.update(criteria=crit, milestones=miles, supporting_habits=habits,
                 supporting_projects=projects, next_action=next_ms,
                 progress=semantics.goal_progress(crit, miles))
        return g

    def project_full(pid: int) -> dict:
        p = db().one("SELECT * FROM projects WHERE id=?", (pid,))
        if not p:
            raise HTTPException(404, "project not found")
        p = dict(p)
        tasks = [dict(r) for r in db().query(
            "SELECT * FROM tasks WHERE project_id=? ORDER BY order_idx, id", (pid,))]
        prog = semantics.project_progress(tasks)
        goal_ids = [r["to_id"] for r in db().query(
            "SELECT to_id FROM links WHERE from_type='project' AND from_id=? AND to_type='goal'", (pid,))]
        goals = [dict(db().one("SELECT id,title FROM goals WHERE id=?", (gid,)))
                 for gid in goal_ids if db().one("SELECT 1 FROM goals WHERE id=?", (gid,))]
        p.update(tasks=tasks, progress=prog, health=project_health(p, prog), linked_goals=goals)
        return p

    def project_health(p: dict, prog: Optional[float]) -> str:
        if p["status"] in ("completed", "archived") or prog == 1.0:
            return "done"
        if (p.get("blockers") or "").strip():
            return "blocked"
        if p.get("deadline") and p["deadline"] < today_str() and (prog or 0) < 1:
            return "at_risk"
        return "on_track"

    def habit_completion_dates(hid: int) -> list[str]:
        return [r["occurred_on"] for r in db().query(
            "SELECT occurred_on FROM habit_completions WHERE habit_id=? AND subtask_id IS NULL", (hid,))]

    def habit_today(hid: int, on: date) -> dict:
        h = dict(db().one("SELECT * FROM habits WHERE id=?", (hid,)))
        subs = [dict(r) for r in db().query(
            "SELECT * FROM habit_subtasks WHERE habit_id=? ORDER BY order_idx, id", (hid,))]
        on_iso = on.isoformat()
        done_sub_ids = {r["subtask_id"] for r in db().query(
            "SELECT subtask_id FROM habit_completions WHERE habit_id=? AND occurred_on=? AND subtask_id IS NOT NULL",
            (hid, on_iso))}
        parent_done_row = db().one(
            "SELECT 1 FROM habit_completions WHERE habit_id=? AND occurred_on=? AND subtask_id IS NULL",
            (hid, on_iso))
        for s in subs:
            s["done_today"] = s["id"] in done_sub_ids
        if subs:
            done_today = all(s["done_today"] for s in subs)
        else:
            done_today = parent_done_row is not None
        comp_dates = habit_completion_dates(hid)
        pending = semantics.due_on(h, on, comp_dates) and not done_today
        goal = None
        if h.get("supports_goal_id"):
            gr = db().one("SELECT id,title FROM goals WHERE id=?", (h["supports_goal_id"],))
            goal = dict(gr) if gr else None
        h.update(subtasks=subs, done_today=done_today, pending=pending, supports_goal=goal)
        return h

    # ---------------- static / auth ----------------
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/session")
    def session(request: Request):
        return {"principal": auth.principal(request)}

    @app.post("/api/login")
    def login(body: LoginIn, response: Response):
        import hmac
        if not hmac.compare_digest(body.secret, settings.session_secret):
            raise HTTPException(401, "bad unlock secret")
        response.set_cookie(
            auth.COOKIE, auth.sign_session(settings.session_secret),
            httponly=True, samesite="lax", secure=False, path="/")
        return {"ok": True, "principal": "session"}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(auth.COOKIE, path="/")
        return {"ok": True}

    # ---------------- reads ----------------
    @app.get("/api/today")
    def get_today(request: Request, on_param: str | None = Query(None, alias="date"), _=reader):
        on = semantics.parse_date(on_param) if on_param else date.today()
        on_iso = on.isoformat()
        habits = []
        for r in db().query("SELECT id FROM habits WHERE status='active'"):
            e = habit_today(r["id"], on)
            if e["pending"] or e["done_today"]:
                habits.append(e)
        habits.sort(key=lambda h: (h["done_today"], h["title"]))
        goal_actions = []
        for g in db().query("SELECT * FROM goals WHERE status='active'"):
            gid = g["id"]
            nm = db().one(
                "SELECT title FROM milestones WHERE goal_id=? AND status!='completed' ORDER BY order_idx, id LIMIT 1",
                (gid,))
            review_due = bool(g["review_on"] and g["review_on"] <= on_iso)
            if nm or review_due:
                goal_actions.append({"goal_id": gid, "goal": g["title"],
                                     "next_action": nm["title"] if nm else None,
                                     "review_due": review_due, "review_on": g["review_on"]})
        proj_actions = [{"project_id": p["id"], "project": p["title"], "next_action": p["next_action"],
                         "deadline": p["deadline"], "blockers": p["blockers"]}
                        for p in db().query(
                            "SELECT * FROM projects WHERE status='active' AND next_action IS NOT NULL")]
        completed = sum(1 for h in habits if h["done_today"])
        return {"date": on_iso, "habits": habits, "goal_next_actions": goal_actions,
                "project_next_actions": proj_actions,
                "counts": {"habits_total": len(habits), "habits_completed": completed,
                           "progress": None if not habits else round(completed / len(habits), 4)}}

    @app.get("/api/goals")
    def get_goals(_=reader):
        ids = [r["id"] for r in db().query("SELECT id FROM goals ORDER BY life_area, id")]
        return {"goals": [goal_full(i) for i in ids]}

    @app.get("/api/projects")
    def get_projects(_=reader):
        ids = [r["id"] for r in db().query("SELECT id FROM projects ORDER BY status, id")]
        return {"projects": [project_full(i) for i in ids]}

    @app.get("/api/metrics")
    def get_metrics(_=reader):
        return {"metrics": [dict(r) for r in db().query("SELECT * FROM metrics ORDER BY label")]}

    @app.get("/api/data/summary")
    def data_summary(_=reader):
        end = date.today()
        start = end - timedelta(days=29)
        goals = []
        for g in db().query("SELECT id,title,status FROM goals WHERE status IN ('active','blocked','paused')"):
            gid = g["id"]
            crit = [dict(r) for r in db().query("SELECT met FROM success_criteria WHERE goal_id=?", (gid,))]
            miles = [dict(r) for r in db().query("SELECT status FROM milestones WHERE goal_id=?", (gid,))]
            goals.append({"title": g["title"], "status": g["status"],
                          "progress": semantics.goal_progress(crit, miles)})
        projects = []
        for p in db().query("SELECT id,title,status FROM projects WHERE status IN ('active','blocked')"):
            tasks = [dict(r) for r in db().query("SELECT done FROM tasks WHERE project_id=?", (p["id"],))]
            projects.append({"title": p["title"], "status": p["status"],
                             "progress": semantics.project_progress(tasks)})
        habits = []
        for h in db().query("SELECT * FROM habits WHERE status='active'"):
            st = semantics.habit_stats(dict(h), habit_completion_dates(h["id"]), start, end)
            habits.append({"title": h["title"], "schedule": h["schedule"], **st})
        metrics = []
        for m in db().query("SELECT * FROM metrics"):
            obs = [dict(r) for r in db().query(
                "SELECT * FROM observations WHERE metric_id=?", (m["id"],))]
            agg = semantics.aggregate_metric(dict(m), obs, start, end)
            metrics.append({"slug": m["slug"], "label": m["label"], **agg})
        return {"range_days": 30, "goals": goals, "projects": projects,
                "habits": habits, "metrics": metrics}

    @app.get("/api/data/metric/{slug}")
    def data_metric(slug: str, range: int = 30, _=reader):
        if range not in (7, 30, 90, 365):
            raise HTTPException(400, "range must be 7|30|90|365")
        m = db().one("SELECT * FROM metrics WHERE slug=?", (slug,))
        if not m:
            raise HTTPException(404, "metric not found")
        m = dict(m)
        end = date.today()
        start = end - timedelta(days=range - 1)
        obs = [dict(r) for r in db().query(
            "SELECT * FROM observations WHERE metric_id=? ORDER BY occurred_on", (m["id"],))]
        inrange = [o for o in obs if start <= semantics.parse_date(o["occurred_on"]) <= end]
        agg = semantics.aggregate_metric(m, obs, start, end)
        chart = m.get("chart") or _default_chart(m["value_type"])
        series = [{"date": o["occurred_on"], "value": o["value"],
                   "estimated": bool(o["estimated"])} for o in inrange]
        return {"metric": m, "range": range, "chart_type": chart, "aggregate": agg,
                "series": series, "coverage": agg.get("coverage", len(series))}

    # ---------------- completion toggles (session or agent) ----------------
    @app.post("/api/complete")
    def complete(body: CompleteIn, _=reader):
        d = body.date
        with db().lock:
            if body.kind == "task":
                t = db().one("SELECT * FROM tasks WHERE id=?", (body.id,))
                if not t:
                    raise HTTPException(404, "task not found")
                db().conn.execute("UPDATE tasks SET done=?, completed_at=? WHERE id=?",
                                  (1 if body.done else 0, now() if body.done else None, body.id))
                return dict(db().one("SELECT * FROM tasks WHERE id=?", (body.id,)))
            if body.kind == "subtask":
                s = db().one("SELECT * FROM habit_subtasks WHERE id=?", (body.id,))
                if not s:
                    raise HTTPException(404, "subtask not found")
                hid, sid = s["habit_id"], body.id
                _toggle_completion(hid, sid, d, body.done)
                return habit_today(hid, semantics.parse_date(d))
            # habit
            h = db().one("SELECT * FROM habits WHERE id=?", (body.id,))
            if not h:
                raise HTTPException(404, "habit not found")
            _toggle_completion(body.id, None, d, body.done)
            return habit_today(body.id, semantics.parse_date(d))

    def _toggle_completion(hid: int, sid: Optional[int], d: str, done: bool):
        exists = db().one(
            "SELECT id FROM habit_completions WHERE habit_id=? AND occurred_on=? AND "
            + ("subtask_id=?" if sid is not None else "subtask_id IS NULL"),
            (hid, d, sid) if sid is not None else (hid, d))
        if done and not exists:
            db().conn.execute(
                "INSERT INTO habit_completions(habit_id,subtask_id,occurred_on,created_at) VALUES(?,?,?,?)",
                (hid, sid, d, now()))
        elif not done and exists:
            db().conn.execute("DELETE FROM habit_completions WHERE id=?", (exists["id"],))

    # ---------------- writes (agent only) ----------------
    @app.post("/api/goals", status_code=201)
    def create_goal(body: GoalIn, _=agent):
        if db().one("SELECT 1 FROM goals WHERE slug=?", (body.slug,)):
            raise HTTPException(409, "slug exists")
        ts = now()
        gid = db().write(
            "INSERT INTO goals(slug,title,life_area,outcome,definition_of_done,status,target_date,"
            "review_on,status_reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (body.slug, body.title, body.life_area, body.outcome, body.definition_of_done,
             body.status, body.target_date, body.review_on, body.status_reason, ts, ts))
        return goal_full(gid)

    @app.patch("/api/goals/{gid}")
    def patch_goal(gid: int, body: GoalPatch, _=agent):
        _apply_patch("goals", gid, body, touch=True)
        return goal_full(gid)

    @app.post("/api/goals/{gid}/criteria", status_code=201)
    def add_criterion(gid: int, body: CriterionIn, _=agent):
        if not db().one("SELECT 1 FROM goals WHERE id=?", (gid,)):
            raise HTTPException(404, "goal not found")
        db().write("INSERT INTO success_criteria(goal_id,text,met,measure) VALUES(?,?,?,?)",
                   (gid, body.text, 1 if body.met else 0, body.measure))
        _touch_goal(gid)
        return goal_full(gid)

    @app.patch("/api/criteria/{cid}")
    def patch_criterion(cid: int, body: CriterionPatch, _=agent):
        row = db().one("SELECT goal_id FROM success_criteria WHERE id=?", (cid,))
        if not row:
            raise HTTPException(404, "criterion not found")
        _apply_patch("success_criteria", cid, body, bool_fields={"met"})
        _touch_goal(row["goal_id"])
        return goal_full(row["goal_id"])

    @app.post("/api/goals/{gid}/milestones", status_code=201)
    def add_milestone(gid: int, body: MilestoneIn, _=agent):
        if not db().one("SELECT 1 FROM goals WHERE id=?", (gid,)):
            raise HTTPException(404, "goal not found")
        ca = now() if body.status == "completed" else None
        db().write("INSERT INTO milestones(goal_id,title,status,order_idx,completed_at) VALUES(?,?,?,?,?)",
                   (gid, body.title, body.status, body.order_idx, ca))
        _touch_goal(gid)
        return goal_full(gid)

    @app.patch("/api/milestones/{mid}")
    def patch_milestone(mid: int, body: MilestonePatch, _=agent):
        row = db().one("SELECT goal_id FROM milestones WHERE id=?", (mid,))
        if not row:
            raise HTTPException(404, "milestone not found")
        data = body.model_dump(exclude_unset=True)
        if data.get("status") == "completed":
            data["completed_at"] = now()
        elif "status" in data:
            data["completed_at"] = None
        _apply_data("milestones", mid, data)
        _touch_goal(row["goal_id"])
        return goal_full(row["goal_id"])

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectIn, _=agent):
        if db().one("SELECT 1 FROM projects WHERE slug=?", (body.slug,)):
            raise HTTPException(409, "slug exists")
        pid = db().write(
            "INSERT INTO projects(slug,title,status,phase,deadline,next_action,blockers,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (body.slug, body.title, body.status, body.phase, body.deadline,
             body.next_action, body.blockers, now()))
        return project_full(pid)

    @app.patch("/api/projects/{pid}")
    def patch_project(pid: int, body: ProjectPatch, _=agent):
        _apply_patch("projects", pid, body, touch=True)
        return project_full(pid)

    @app.post("/api/projects/{pid}/tasks", status_code=201)
    def add_task(pid: int, body: TaskIn, _=agent):
        if not db().one("SELECT 1 FROM projects WHERE id=?", (pid,)):
            raise HTTPException(404, "project not found")
        db().write("INSERT INTO tasks(project_id,title,done,order_idx) VALUES(?,?,0,?)",
                   (pid, body.title, body.order_idx))
        _touch_project(pid)
        return project_full(pid)

    @app.patch("/api/tasks/{tid}")
    def patch_task(tid: int, body: TaskPatch, _=agent):
        row = db().one("SELECT project_id FROM tasks WHERE id=?", (tid,))
        if not row:
            raise HTTPException(404, "task not found")
        data = body.model_dump(exclude_unset=True)
        if "done" in data:
            data["done"] = 1 if data["done"] else 0
            data["completed_at"] = now() if data["done"] else None
        _apply_data("tasks", tid, data)
        _touch_project(row["project_id"])
        return project_full(row["project_id"])

    @app.post("/api/habits", status_code=201)
    def create_habit(body: HabitIn, _=agent):
        if db().one("SELECT 1 FROM habits WHERE slug=?", (body.slug,)):
            raise HTTPException(409, "slug exists")
        detail = schedule_detail_validate(body.schedule, body.schedule_detail)
        if body.supports_goal_id and not db().one("SELECT 1 FROM goals WHERE id=?", (body.supports_goal_id,)):
            raise HTTPException(400, "supports_goal_id not found")
        hid = db().write(
            "INSERT INTO habits(slug,title,schedule,schedule_detail,status,supports_goal_id,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (body.slug, body.title, body.schedule, detail, body.status, body.supports_goal_id, now()))
        return dict(db().one("SELECT * FROM habits WHERE id=?", (hid,)))

    @app.patch("/api/habits/{hid}")
    def patch_habit(hid: int, body: HabitPatch, _=agent):
        row = db().one("SELECT * FROM habits WHERE id=?", (hid,))
        if not row:
            raise HTTPException(404, "habit not found")
        data = body.model_dump(exclude_unset=True)
        if "schedule_detail" in data or "schedule" in data:
            sched = data.get("schedule", row["schedule"])
            data["schedule_detail"] = schedule_detail_validate(sched, data.get(
                "schedule_detail", json.loads(row["schedule_detail"] or "{}")))
        _apply_data("habits", hid, data)
        return dict(db().one("SELECT * FROM habits WHERE id=?", (hid,)))

    @app.post("/api/habits/{hid}/subtasks", status_code=201)
    def add_subtask(hid: int, body: SubtaskIn, _=agent):
        if not db().one("SELECT 1 FROM habits WHERE id=?", (hid,)):
            raise HTTPException(404, "habit not found")
        db().write("INSERT INTO habit_subtasks(habit_id,title,order_idx) VALUES(?,?,?)",
                   (hid, body.title, body.order_idx))
        return {"habit_id": hid, "subtasks": [dict(r) for r in db().query(
            "SELECT * FROM habit_subtasks WHERE habit_id=? ORDER BY order_idx, id", (hid,))]}

    @app.post("/api/metrics", status_code=201)
    def create_metric(body: MetricIn, _=agent):
        if db().one("SELECT 1 FROM metrics WHERE slug=?", (body.slug,)):
            raise HTTPException(409, "slug exists")
        mid = db().write(
            "INSERT INTO metrics(slug,label,value_type,unit,range_spec,aggregation,chart,privacy,"
            "missing_means,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (body.slug, body.label, body.value_type, body.unit, body.range_spec, body.aggregation,
             body.chart, body.privacy, body.missing_means, now()))
        return dict(db().one("SELECT * FROM metrics WHERE id=?", (mid,)))

    @app.patch("/api/metrics/{mid}")
    def patch_metric(mid: int, body: MetricPatch, _=agent):
        if not db().one("SELECT 1 FROM metrics WHERE id=?", (mid,)):
            raise HTTPException(404, "metric not found")
        _apply_data("metrics", mid, body.model_dump(exclude_unset=True))
        return dict(db().one("SELECT * FROM metrics WHERE id=?", (mid,)))

    @app.post("/api/metrics/{slug}/observations", status_code=201)
    def add_observation(slug: str, body: ObservationIn, _=agent):
        m = db().one("SELECT * FROM metrics WHERE slug=?", (slug,))
        if not m:
            raise HTTPException(404, "metric not found")
        obs_value_validate(dict(m), body.value)
        oid = db().write(
            "INSERT INTO observations(metric_id,occurred_on,value,note,estimated,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (m["id"], body.occurred_on, body.value, body.note, 1 if body.estimated else 0, now()))
        return dict(db().one("SELECT * FROM observations WHERE id=?", (oid,)))

    @app.patch("/api/observations/{oid}")
    def patch_observation(oid: int, body: ObservationPatch, _=agent):
        row = db().one("SELECT * FROM observations WHERE id=?", (oid,))
        if not row:
            raise HTTPException(404, "observation not found")
        data = body.model_dump(exclude_unset=True)
        if "value" in data:
            m = db().one("SELECT * FROM metrics WHERE id=?", (row["metric_id"],))
            obs_value_validate(dict(m), data["value"])
        if "estimated" in data:
            data["estimated"] = 1 if data["estimated"] else 0
        _apply_data("observations", oid, data)
        return dict(db().one("SELECT * FROM observations WHERE id=?", (oid,)))

    @app.delete("/api/observations/{oid}")
    def delete_observation(oid: int, _=agent):
        row = db().one("SELECT * FROM observations WHERE id=?", (oid,))  # read before delete
        if not row:
            raise HTTPException(404, "observation not found")
        before = dict(row)
        db().write("DELETE FROM observations WHERE id=?", (oid,))
        return {"deleted": before}

    @app.post("/api/links", status_code=201)
    def create_link(body: LinkIn, _=agent):
        if db().one("SELECT id FROM links WHERE from_type=? AND from_id=? AND to_type=? AND to_id=?",
                    (body.from_type, body.from_id, body.to_type, body.to_id)):
            raise HTTPException(409, "link exists")
        lid = db().write("INSERT INTO links(from_type,from_id,to_type,to_id) VALUES(?,?,?,?)",
                         (body.from_type, body.from_id, body.to_type, body.to_id))
        return dict(db().one("SELECT * FROM links WHERE id=?", (lid,)))

    @app.delete("/api/links/{lid}")
    def delete_link(lid: int, _=agent):
        row = db().one("SELECT * FROM links WHERE id=?", (lid,))
        if not row:
            raise HTTPException(404, "link not found")
        db().write("DELETE FROM links WHERE id=?", (lid,))
        return {"deleted": dict(row)}

    # ---- patch helpers ----
    def _apply_patch(table: str, rid: int, body: BaseModel, *, touch=False, bool_fields=frozenset()):
        if not db().one(f"SELECT 1 FROM {table} WHERE id=?", (rid,)):
            raise HTTPException(404, "not found")
        data = body.model_dump(exclude_unset=True)
        for b in bool_fields:
            if b in data:
                data[b] = 1 if data[b] else 0
        if touch:
            data["updated_at"] = now()
        _apply_data(table, rid, data)

    def _apply_data(table: str, rid: int, data: dict):
        if not data:
            return
        cols = ", ".join(f"{k}=?" for k in data)
        with db().lock:
            db().conn.execute(f"UPDATE {table} SET {cols} WHERE id=?", (*data.values(), rid))

    def _touch_goal(gid: int):
        db().write("UPDATE goals SET updated_at=? WHERE id=?", (now(), gid))

    def _touch_project(pid: int):
        db().write("UPDATE projects SET updated_at=? WHERE id=?", (now(), pid))

    @app.on_event("shutdown")
    def _shutdown():
        app.state.db.close()

    return app


def _default_chart(value_type: str) -> str:
    return {"numeric": "line", "duration": "line", "rating": "line", "count": "bar",
            "boolean": "calendar", "categorical": "categorical-history", "text": "count"}.get(
        value_type, "line")


# Served by uvicorn as a factory (no import-time side effects):
#   uvicorn app.main:create_app --factory

