"""The static page, script, and style that `watch` serves. The script polls `/api/run` and renders text nodes only."""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Autoimprove run · skillz-that-grillz</title>
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="stylesheet" href="/bulma.min.css">
<link rel="stylesheet" href="/app.css">
</head>
<body>
<header class="masthead">
  <div class="masthead-copy">
    <div class="brand"><img src="/favicon.png" alt="" width="32" height="32"><span>skillz-that-grillz</span></div>
    <h1>Autoimprove run</h1>
    <p id="state" role="status">Loading...</p>
  </div>
</header>
<p id="stale" role="status" hidden>Stale data. Latest update unavailable.</p>
<main>
<section><h2>Status</h2><div id="status"></div></section>
<section><h2>Roles</h2><div id="roles"></div></section>
<section><h2>Leaderboard</h2><div id="leaderboard"></div></section>
<section><h2>Cases</h2><div id="cases"></div></section>
<section><h2>Reflections</h2><div id="reflections"></div></section>
<section><h2>Outcomes</h2><div id="outcomes"></div></section>
</main>
<script src="/app.js"></script>
</body>
</html>
"""

SCRIPT = """"use strict";
const opened = new Set();
let shown = null;

function el(tag, attributes, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes || {})) node.setAttribute(key, String(value));
  for (const child of children.flat()) {
    if (child !== null && child !== undefined) node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

const num = (value, digits = 0) => (value === null || value === undefined ? "-" : Number(value).toFixed(digits));
const text = (value) => (value === null || value === undefined || value === "" ? "-" : String(value));

function table(headers, rows, kind = "") {
  if (!rows.length) return el("p", {class: "empty"}, "No data yet.");
  return el("div", {class: "table-scroll"}, el("table", {class: `table is-fullwidth ${kind}`},
    el("thead", {}, el("tr", {}, headers.map((h) => el("th", {}, h)))), el("tbody", {}, rows)));
}

const row = (cells, attributes) => el("tr", attributes, cells.map((cell) => el("td", {}, cell)));

function meter(label, value, cap) {
  const bar = el("progress", {class: "progress is-small", max: cap || 1, value: Math.min(value || 0, cap || 1)});
  return el("div", {class: "meter"}, el("span", {}, `${label}: ${num(value)} / ${num(cap)}`), bar);
}

function fold(key, summary, body, attributes) {
  const node = el("details", attributes, el("summary", {}, summary), body);
  node.open = opened.has(key);
  node.addEventListener("toggle", () => (node.open ? opened.add(key) : opened.delete(key)));
  return node;
}

function status(s) {
  const gate = s.gate_verdict ? `${s.gate_verdict}, delta ${num(s.gate_delta, 3)}, SE ${num(s.gate_se, 3)}` : "not run";
  const comparisons = s.comparisons.map((c) => row([text(c.challenger), text(c.incumbent), text(c.verdict),
    num(c.delta, 3), num(c.se, 3)]));
  return [el("div", {class: "meters"}, meter("Calls", s.calls, s.call_cap),
    el("div", {class: "meter"}, el("span", {}, `Accumulated call seconds: ${num(s.seconds)}. Wall-clock budget: ${num(s.seconds_cap)} s.`))),
    el("p", {}, `Model ${text(s.model)}. Search stop: ${text(s.stop_trigger)} (${text(s.search_reason)}). Gate: ${gate}.`),
    el("h3", {}, "Validation comparisons"),
    table(["Challenger", "Incumbent", "Verdict", "Delta", "SE"], comparisons, "comparison-table")];
}

function roles(items) {
  return table(["Role", "Calls", "Seconds", "Outcomes", "Input tokens", "Cached", "Output tokens", "Mean latency s"],
    items.map((r) => row([r.role, num(r.calls), num(r.seconds, 1), r.outcomes, num(r.input_tokens), num(r.cached_input_tokens),
      num(r.output_tokens), num(r.mean_latency, 1)])), "roles-table");
}

function leaderboard(items) {
  return table(["Split", "Arm", "Candidate", "Cases", "Runs", "Scored", "Mean score", "Zero runs"],
    items.map((r) => row([r.split, r.arm, r.candidate, r.cases, r.runs, r.scored, num(r.mean, 3), r.zero_runs])),
    "leaderboard-table");
}

function criteria(items, aggregated) {
  const headers = aggregated ? ["Criterion", "Unmet / runs", "Lowest level", "Judge reason"]
    : ["Criterion", "Level", "Met", "Judge reason"];
  return table(headers, items.map((c) => row(aggregated
    ? [c.id, `${c.unmet} / ${c.runs}`, num(c.lowest_level), text(c.reason)]
    : [c.id, num(c.level), c.met ? "yes" : "no", text(c.reason)], {class: (aggregated ? c.unmet : !c.met) ? "unmet" : ""})),
    "criteria-table");
}

function cases(items) {
  if (!items.length) return el("p", {class: "empty"}, "No data yet.");
  return items.map((c) => {
    const unmet = c.criteria.filter((x) => x.unmet).length;
    const faults = c.statuses.length ? ` Faults: ${c.statuses.join(", ")}.` : "";
    const summary = `${c.case_id} (${c.splits.join(", ")}): mean ${num(c.mean, 3)}, ${c.zero_runs} of ${c.runs} runs at 0, `
      + `${unmet} criteria unmet.${faults}`;
    const bad = c.runs > 0 && c.zero_runs === c.runs ? {class: "zero"} : {};
    return fold(`case:${c.case_id}`, summary, criteria(c.criteria, true), bad);
  });
}

function reflections(items) {
  if (!items.length) return el("p", {class: "empty"}, "No reflection yet.");
  return el("ol", {}, items.map((r) => el("li", {}, el("strong", {}, text(r.hypothesis)), el("p", {}, text(r.notes)),
    el("p", {class: "meta"}, `${text(r.parent)} to ${text(r.proposed)}, ${num(r.changed)} files changed, `
      + `${num(r.latency, 1)} s, ${num(r.output_tokens)} output tokens${r.over_budget ? ", over budget" : ""}`
      + `${r.rejected ? `, rejected: ${r.rejected}` : ""}`))));
}

function outcomes(items, total) {
  const note = el("p", {class: "meta"}, `${items.length} newest of ${total} outcomes, newest first.`);
  return [note, ...items.map((o) => fold(`outcome:${o.index}`,
    `#${o.index} ${text(o.case_id)} ${text(o.split)} ${text(o.arm)} ${text(o.candidate)} repeat ${num(o.repeat)}: `
      + `score ${num(o.score, 3)}${o.status ? ` (${o.status})` : ""}, ${num(o.latency, 1)} s`,
    criteria(o.criteria, false)))];
}

function put(id, content) {
  document.getElementById(id).replaceChildren(...[content].flat());
}

function setStale(unavailable) {
  document.getElementById("stale").hidden = !unavailable || shown === null;
}

function render(data) {
  const state = document.getElementById("state");
  if (data.state !== "ok") {
    state.textContent = data.state === "waiting" ? `Waiting for ${data.path}` : `Cannot read ${data.path}: ${data.error}`;
    setStale(true);
    return;
  }
  const s = data.status;
  state.textContent = `${text(s.run_id)}: phase ${text(s.phase)}, updated ${new Date(data.modified * 1000).toLocaleTimeString()}`;
  setStale(false);
  if (shown === data.modified) return;
  shown = data.modified;
  put("status", status(s));
  put("roles", roles(data.roles));
  put("leaderboard", leaderboard(data.leaderboard));
  put("cases", cases(data.cases));
  put("reflections", reflections(data.reflections));
  put("outcomes", outcomes(data.outcomes, data.outcome_count));
}

async function poll() {
  try {
    const capability = location.hash.slice(1);
    if (!capability) {
      document.getElementById("state").textContent = "This page needs the private link printed by watch.";
      setStale(true);
      return;
    }
    const response = await fetch("/api/run", {cache: "no-store", headers: {"X-Watch-Capability": capability}});
    render(await response.json());
  } catch (error) {
    document.getElementById("state").textContent = `The watcher does not answer: ${error}`;
    setStale(true);
  } finally {
    setTimeout(poll, 2000);
  }
}

poll();
"""

STYLE = """@font-face {
  font-family: Inter;
  src: url("/inter-latin-wght-normal.woff2") format("woff2");
  font-style: normal;
  font-weight: 100 900;
  font-display: swap;
}
:root {
  color-scheme: light;
  --page: #f6f8fb; --surface: #fff; --ink: #172033; --muted: #556179;
  --line: #dbe1ea; --accent: #2459a6; --tint: #edf3fc; --bad: #fff0ea;
  --zero: #a63822; --focus: #2459a6;
  --bulma-body-background-color: var(--page);
  --bulma-text: var(--ink);
  --bulma-table-background-color: var(--surface);
  --bulma-table-color: var(--ink);
  --bulma-table-cell-border-color: var(--line);
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --page: #101722; --surface: #1a2432; --ink: #eaf0f8; --muted: #b5c1d3;
    --line: #354358; --accent: #94bcff; --tint: #24354d; --bad: #3b2929;
    --zero: #ffae9a; --focus: #94bcff;
  }
}
html { background: var(--page); color: var(--ink); }
body { max-width: 1280px; margin: 0 auto; padding: 28px 24px 64px;
  background: var(--page); color: var(--ink);
  font: 15px/1.55 Inter, system-ui, sans-serif; }
.masthead { display: flex; align-items: center; justify-content: space-between; gap: 32px;
  min-width: 0; padding: 24px 28px; border: 1px solid var(--line); border-radius: 12px;
  background: var(--surface); }
.masthead-copy { min-width: 0; }
.brand { display: flex; align-items: center; gap: 10px; color: var(--accent);
  font-size: .85rem; font-weight: 700; letter-spacing: .01em; }
.brand img { width: 32px; height: 32px; border-radius: 7px; }
h1 { color: var(--ink); font-size: clamp(1.55rem, 3vw, 2.15rem);
  font-weight: 750; line-height: 1.2; margin: 12px 0 6px; }
#state, .meta, .empty { color: var(--muted); overflow-wrap: anywhere; }
#state { margin: 0; }
#stale { position: sticky; top: 8px; z-index: 2; margin: 12px 0 0; padding: 10px 14px;
  border: 1px solid var(--zero); border-radius: 7px; background: var(--bad); color: var(--ink);
  font-weight: 700; overflow-wrap: anywhere; }
main { display: grid; gap: 18px; margin-top: 18px; min-width: 0; }
section { min-width: 0; padding: 22px 26px; border: 1px solid var(--line);
  border-radius: 10px; background: var(--surface); }
h2 { color: var(--ink); font-size: 1.05rem; font-weight: 750; margin: 0 0 16px; }
h3 { color: var(--ink); font-size: .95rem; font-weight: 700; margin: 20px 0 10px; }
p { overflow-wrap: anywhere; }
.table-scroll { max-width: 100%; overflow-x: auto; }
.table { min-width: 760px; table-layout: fixed; margin: 8px 0 0;
  background: var(--surface); color: var(--ink); }
.table th, .table td { padding: 10px 12px; border-color: var(--line);
  vertical-align: top; line-height: 1.45; }
.table th { color: var(--muted); background: var(--tint);
  font-size: .76rem; font-weight: 750; letter-spacing: .025em; }
.table td { color: var(--ink); overflow-wrap: anywhere; }
.table tbody tr:hover td { background: var(--tint); }
.table tr.unmet td, .table tr.unmet:hover td { background: var(--bad); }
.roles-table, .leaderboard-table { min-width: 1080px; }
.criteria-table th:nth-child(1) { width: 20%; }
.criteria-table th:nth-child(2) { width: 18%; }
.criteria-table th:nth-child(3) { width: 17%; }
.roles-table :is(th, td):nth-child(n+2),
.leaderboard-table :is(th, td):nth-child(n+4),
.comparison-table :is(th, td):nth-child(n+4),
.criteria-table :is(th, td):nth-child(2) {
  text-align: right; font-variant-numeric: tabular-nums; }
details { border-top: 1px solid var(--line); padding: 5px 0; }
details.zero > summary { color: var(--zero); font-weight: 700; }
summary { cursor: pointer; padding: 9px 6px; border-radius: 5px; overflow-wrap: anywhere; }
summary:hover { background: var(--tint); }
summary:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.meters { display: flex; flex-wrap: wrap; gap: 20px; }
.meter { display: flex; flex-direction: column; gap: 6px; min-width: min(240px, 100%); }
.progress { width: 100%; accent-color: var(--accent); }
li { margin-bottom: 14px; overflow-wrap: anywhere; }
li p { margin: 3px 0; }
@media (max-width: 600px) {
  body { padding: 12px 12px 36px; }
  .masthead { display: block; padding: 18px; }
  section { padding: 17px; }
}
"""
