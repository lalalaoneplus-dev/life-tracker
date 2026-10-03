"use strict";
// life-tracker frontend. Reads + completion toggles only; agent owns writes.

const $ = (s, r = document) => r.querySelector(s);
const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };
// Format a Date's LOCAL calendar day as YYYY-MM-DD (not UTC — toISOString would skew the day).
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
// "Today" is the browser's local calendar day. en-CA gives YYYY-MM-DD.
const todayISO = () => new Date().toLocaleDateString("en-CA");

let state = { view: "today", date: todayISO(), metric: null, range: 30, poll: null };

async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...opts });
  if (r.status === 401) { showLogin(); throw new Error("unauthorized"); }
  if (!r.ok) throw new Error("http " + r.status);
  return r.status === 204 ? null : r.json();
}
function setSync(t) { $("#sync").textContent = t; if (t) setTimeout(() => { if ($("#sync").textContent === t) $("#sync").textContent = ""; }, 1500); }
function offline(on) { $("#offline").hidden = !on; }

// ---------- auth ----------
function showLogin() { $("#app").hidden = true; $("#view-login").hidden = false; }
async function boot() {
  try {
    const s = await api("/api/session");
    if (!s.principal) return showLogin();
    $("#view-login").hidden = true; $("#app").hidden = false;
    switchView(state.view); startPolling();
  } catch (e) { if (e.message !== "unauthorized") offline(true); }
}
$("#login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault(); $("#login-error").hidden = true;
  const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ secret: $("#secret").value }) });
  if (r.ok) { $("#secret").value = ""; boot(); } else { $("#login-error").hidden = false; }
});

// ---------- navigation ----------
document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => switchView(t.dataset.view)));
function switchView(v) {
  state.view = v;
  document.querySelectorAll(".tab").forEach(t => t.setAttribute("aria-current", t.dataset.view === v));
  document.querySelectorAll("#app .view").forEach(s => s.hidden = (s.id !== "view-" + v));
  ({ today: loadToday, goals: loadGoals, projects: loadProjects, data: loadData }[v])();
}

function loading(node) { node.replaceChildren(el("p", "muted", "Loading…")); }
function empty(node, msg) { node.replaceChildren(el("p", "muted", msg)); }
function errorRetry(node, fn) {
  node.replaceChildren();
  const p = el("p", "error", "Could not load. ");
  const b = el("button", null, "Retry"); b.type = "button"; b.onclick = fn; p.append(b); node.append(p);
}

// ---------- TODAY ----------
$("#day-prev").onclick = () => { shiftDay(-1); };
$("#day-next").onclick = () => { shiftDay(1); };
$("#day-today").onclick = () => { state.date = todayISO(); loadToday(); };
function shiftDay(n) { const d = new Date(state.date + "T00:00:00"); d.setDate(d.getDate() + n); state.date = iso(d); loadToday(); }

async function loadToday() {
  const body = $("#today-body"); loading(body);
  $("#day-label").textContent = state.date;
  try {
    const t = await api("/api/today?date=" + state.date); offline(false);
    const c = t.counts;
    $("#today-progress").textContent = c.habits_total
      ? `${c.habits_completed}/${c.habits_total} done` : "";
    body.replaceChildren();
    if (!t.habits.length && !t.goal_next_actions.length && !t.project_next_actions.length)
      return empty(body, "Nothing scheduled. Enjoy the day.");
    if (t.habits.length && c.habits_completed === c.habits_total)
      body.append(el("p", "done-banner", "All habits complete for the day. ✓"));

    if (t.habits.length) {
      const h = el("h3", null, "Habits"); body.append(h);
      t.habits.forEach(hb => body.append(habitCard(hb)));
    }
    if (t.goal_next_actions.length) {
      body.append(el("h3", null, "Goal next actions"));
      t.goal_next_actions.forEach(g => {
        const d = el("div", "row muted");
        d.textContent = (g.next_action || "Review due") + " — " + g.goal + (g.review_due ? " (review)" : "");
        body.append(d);
      });
    }
    if (t.project_next_actions.length) {
      body.append(el("h3", null, "Project next actions"));
      t.project_next_actions.forEach(p => {
        const d = el("div", "row muted");
        d.textContent = (p.next_action || "") + " — " + p.project + (p.blockers ? " ⚠ " + p.blockers : "");
        body.append(d);
      });
    }
  } catch (e) { if (e.message === "unauthorized") return; offline(true); errorRetry(body, loadToday); }
}

function habitCard(hb) {
  const card = el("div", "card" + (hb.done_today ? " complete" : ""));
  const head = el("div", "row");
  const box = checkbox(hb.done_today, hb.subtasks.length > 0, async (val) => {
    await api("/api/complete", { method: "POST", body: JSON.stringify({ kind: "habit", id: hb.id, date: state.date, done: val }) });
    setSync("saved"); loadToday();
  });
  head.append(box);
  const title = el("span", "title", hb.title);
  head.append(title);
  if (hb.supports_goal) head.append(el("span", "chip", "→ " + hb.supports_goal.title));
  card.append(head);
  if (hb.subtasks.length) {
    const ul = el("div", "subs");
    hb.subtasks.forEach(s => {
      const r = el("label", "row sub");
      r.append(checkbox(s.done_today, false, async (val) => {
        await api("/api/complete", { method: "POST", body: JSON.stringify({ kind: "subtask", id: s.id, date: state.date, done: val }) });
        setSync("saved"); loadToday();
      }));
      r.append(el("span", null, s.title)); ul.append(r);
    });
    card.append(ul);
  }
  return card;
}
function checkbox(checked, parentOfSubs, onToggle) {
  const b = el("button", "check" + (checked ? " on" : ""));
  b.type = "button"; b.setAttribute("role", "checkbox"); b.setAttribute("aria-checked", checked);
  b.textContent = checked ? "✓" : "";
  b.onclick = () => onToggle(!checked);
  return b;
}

// ---------- GOALS ----------
async function loadGoals() {
  const body = $("#goals-body"); loading(body);
  try {
    const { goals } = await api("/api/goals"); offline(false);
    body.replaceChildren();
    const active = goals.filter(g => g.status === "active");
    if (!goals.length) return empty(body, "No goals yet. The agent adds these.");
    const areas = {};
    goals.forEach(g => { (areas[g.life_area || "Unsorted"] ||= []).push(g); });
    Object.entries(areas).forEach(([area, gs]) => {
      body.append(el("h3", "area", area));
      gs.forEach(g => body.append(goalCard(g)));
    });
  } catch (e) { if (e.message === "unauthorized") return; offline(true); errorRetry(body, loadGoals); }
}
function goalCard(g) {
  const card = el("div", "card status-" + g.status);
  card.append(el("div", "title", g.title));
  const meta = el("div", "muted small");
  meta.textContent = [g.status, g.target_date && "target " + g.target_date, g.review_on && "review " + g.review_on]
    .filter(Boolean).join(" · ");
  card.append(meta);
  if (g.outcome) card.append(el("p", "small", "Outcome: " + g.outcome));
  if (g.definition_of_done) card.append(el("p", "small muted", "Done when: " + g.definition_of_done));
  card.append(progressBar(g.progress, "from criteria/milestones"));
  if (g.milestones.length) {
    const ms = el("div", "subs");
    g.milestones.forEach(m => ms.append(el("div", "row sub" + (m.status === "completed" ? " complete" : ""),
      (m.status === "completed" ? "✓ " : "○ ") + m.title)));
    card.append(ms);
  }
  const support = [...g.supporting_habits.map(h => "habit: " + h.title), ...g.supporting_projects.map(p => "project: " + p.title)];
  if (support.length) card.append(el("p", "small muted", "Supported by " + support.join(", ")));
  return card;
}

// ---------- PROJECTS ----------
async function loadProjects() {
  const body = $("#projects-body"); loading(body);
  try {
    const { projects } = await api("/api/projects"); offline(false);
    body.replaceChildren();
    if (!projects.length) return empty(body, "No projects yet.");
    projects.forEach(p => body.append(projectCard(p)));
  } catch (e) { if (e.message === "unauthorized") return; offline(true); errorRetry(body, loadProjects); }
}
function projectCard(p) {
  const card = el("div", "card health-" + p.health);
  card.append(el("div", "title", p.title));
  card.append(el("div", "muted small", [p.status, p.phase, p.deadline && "due " + p.deadline, "health: " + p.health]
    .filter(Boolean).join(" · ")));
  if (p.blockers) card.append(el("p", "small error", "Blocked: " + p.blockers));
  if (p.next_action) card.append(el("p", "small", "Next: " + p.next_action));
  card.append(progressBar(p.progress, "from task scope"));
  if (p.tasks.length) {
    const ts = el("div", "subs");
    p.tasks.forEach(t => {
      const r = el("label", "row sub");
      r.append(checkbox(!!t.done, false, async (val) => {
        await api("/api/complete", { method: "POST", body: JSON.stringify({ kind: "task", id: t.id, date: state.date, done: val }) });
        setSync("saved"); loadProjects();
      }));
      r.append(el("span", t.done ? "complete" : "", t.title)); ts.append(r);
    });
    card.append(ts);
  }
  if (p.linked_goals.length) card.append(el("p", "small muted", "Serves: " + p.linked_goals.map(g => g.title).join(", ")));
  return card;
}

function progressBar(p, note) {
  const wrap = el("div", "pbar-wrap");
  if (p == null) { wrap.append(el("span", "small muted", "No progress signal (" + note + ")")); return wrap; }
  const bar = el("div", "pbar"); const fill = el("div", "pfill"); fill.style.width = Math.round(p * 100) + "%";
  bar.append(fill); wrap.append(bar);
  wrap.append(el("span", "small muted", Math.round(p * 100) + "% " + note));
  return wrap;
}

// ---------- DATA (read only) ----------
document.querySelectorAll("#range-picker button").forEach(b => b.addEventListener("click", () => {
  state.range = +b.dataset.range;
  document.querySelectorAll("#range-picker button").forEach(x => x.setAttribute("aria-current", x === b));
  if (state.metric) drawMetric();
}));
async function loadData() {
  const sum = $("#data-summary"); loading(sum);
  try {
    const s = await api("/api/data/summary"); offline(false);
    sum.replaceChildren();
    sum.append(summaryBlock("Goal progress", s.goals.map(g =>
      `${g.title}: ${g.progress == null ? "—" : Math.round(g.progress * 100) + "%"}`)));
    sum.append(summaryBlock("Project delivery", s.projects.map(p =>
      `${p.title}: ${p.progress == null ? "—" : Math.round(p.progress * 100) + "%"}`)));
    sum.append(summaryBlock("Habit consistency (30d, scheduled days)", s.habits.map(h =>
      `${h.title}: ${h.rate == null ? "n/a" : Math.round(h.rate * 100) + "%"} (${h.completed}/${h.scheduled})`)));

    const picker = $("#metric-picker"); picker.replaceChildren();
    const { metrics } = await api("/api/metrics");
    if (!metrics.length) { picker.append(el("span", "muted small", "No metrics defined.")); $("#chart").replaceChildren(); return; }
    metrics.forEach(m => {
      const b = el("button", "mbtn", m.label); b.type = "button"; b.dataset.slug = m.slug;
      b.onclick = () => { state.metric = m.slug; markMetric(); drawMetric(); };
      picker.append(b);
    });
    if (!state.metric) state.metric = metrics[0].slug;
    markMetric(); drawMetric();
  } catch (e) { if (e.message === "unauthorized") return; offline(true); errorRetry(sum, loadData); }
}
function markMetric() { document.querySelectorAll("#metric-picker button").forEach(b => b.setAttribute("aria-current", b.dataset.slug === state.metric)); }
function summaryBlock(title, lines) {
  const d = el("div", "sumblock"); d.append(el("h4", null, title));
  if (!lines.length) d.append(el("p", "muted small", "None."));
  else lines.forEach(l => d.append(el("div", "small", l)));
  return d;
}
async function drawMetric() {
  const chart = $("#chart"); loading(chart); $("#chart-alt").textContent = "";
  try {
    const d = await api(`/api/data/metric/${state.metric}?range=${state.range}`);
    const cov = d.aggregate.coverage ?? d.series.length;
    renderChart(chart, d);
    const agg = d.aggregate;
    let av = agg.value != null ? `${agg.aggregation}=${agg.value}` : (agg.distribution ? JSON.stringify(agg.distribution) : "no aggregate");
    $("#chart-alt").textContent = `${d.metric.label}: ${d.series.length} points over ${d.range}d, ${cov} observations backing ${av}. Chart: ${d.chart_type}.`;
  } catch (e) { if (e.message === "unauthorized") return; errorRetry(chart, drawMetric); }
}
function renderChart(node, d) {
  node.replaceChildren();
  const pts = d.series;
  if (!pts.length) return empty(node, "No observations in this range.");
  const W = 320, H = 140, pad = 24;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("class", "svgchart");
  svg.setAttribute("role", "img"); svg.setAttribute("aria-label", $("#chart-alt").textContent || d.metric.label);
  const nums = pts.map(p => parseFloat(p.value)).filter(v => !isNaN(v));
  if ((d.chart_type === "line" || d.chart_type === "bar" || d.chart_type === "count") && nums.length) {
    const max = Math.max(...nums, 1), min = Math.min(...nums, 0);
    const x = i => pad + (pts.length === 1 ? (W - 2 * pad) / 2 : i * (W - 2 * pad) / (pts.length - 1));
    const y = v => H - pad - (max === min ? 0.5 : (v - min) / (max - min)) * (H - 2 * pad);
    if (d.chart_type === "line") {
      const path = pts.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(parseFloat(p.value) || 0)}`).join(" ");
      const pe = document.createElementNS(svg.namespaceURI, "path");
      pe.setAttribute("d", path); pe.setAttribute("class", "line"); svg.append(pe);
    } else {
      const bw = Math.max(2, (W - 2 * pad) / pts.length - 2);
      pts.forEach((p, i) => {
        const v = parseFloat(p.value) || 0; const r = document.createElementNS(svg.namespaceURI, "rect");
        r.setAttribute("x", x(i) - bw / 2); r.setAttribute("y", y(v)); r.setAttribute("width", bw);
        r.setAttribute("height", H - pad - y(v)); r.setAttribute("class", "bar"); svg.append(r);
      });
    }
  } else {
    // distribution / calendar / categorical: simple text list fallback
    const counts = {};
    pts.forEach(p => { counts[p.value] = (counts[p.value] || 0) + 1; });
    node.append(el("p", "small muted", "Distribution:"));
    Object.entries(counts).forEach(([k, v]) => node.append(el("div", "small", `${k}: ${v}`)));
    return;
  }
  node.append(svg);
}

// ---------- lifecycle ----------
function startPolling() {
  if (state.poll) clearInterval(state.poll);
  state.poll = setInterval(() => { if (!document.hidden) switchView(state.view); }, 15000);
}
window.addEventListener("focus", () => { if (!$("#app").hidden) switchView(state.view); });
$("#retry").onclick = () => { offline(false); boot(); };
boot();
