"""Render the knowledge graph as a self-contained, offline HTML review page.

``render_report`` embeds the whole ontology as JSON inside a single HTML file
(no CDN, no build step, no server) so it can be opened straight from disk. The
page offers two views: a champion browser with the champion's counter/synergy
neighbours, and an interactive force-directed graph of the relations.

This is a *review* tool for the generated graph, deliberately independent of the
React frontend milestone.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from lolcoach.knowledge.ontology import Champion, Ontology, PositionInfo, Relation

logger = logging.getLogger(__name__)

_JSON_MARKER = "__KG_JSON__"


def _position(entry: PositionInfo) -> dict[str, object]:
    return {
        "position": entry.position,
        "play": entry.play,
        "win_rate": entry.win_rate,
        "pick_rate": entry.pick_rate,
        "ban_rate": entry.ban_rate,
        "tier": entry.tier,
        "rank": entry.rank,
        "roles": list(entry.roles),
    }


def _champion(entry: Champion) -> dict[str, object]:
    return {
        "slug": entry.slug,
        "name": entry.name,
        "champion_id": entry.champion_id,
        "key": entry.key,
        "positions": [_position(p) for p in entry.positions],
    }


def _relation(entry: Relation) -> dict[str, object]:
    return {
        "source": entry.source,
        "target": entry.target,
        "type": entry.type,
        "position": entry.position,
        "weight": entry.weight,
        "confidence": entry.confidence,
        "games": entry.games,
        "win_rate": entry.win_rate,
        "reason": entry.reason,
    }


def build_payload(ontology: Ontology, *, title: str) -> dict[str, object]:
    """Serialize an ontology into the JSON embedded in the report."""
    counters = sum(1 for r in ontology.relations if r.type == "counter")
    synergies = len(ontology.relations) - counters
    return {
        "title": title,
        "meta": {
            "patch": ontology.patch,
            "source": ontology.source,
            "generated_at": ontology.generated_at,
            "champion_count": len(ontology.champions),
            "relation_count": len(ontology.relations),
            "counter_count": counters,
            "synergy_count": synergies,
        },
        "champions": [_champion(c) for c in ontology.champions.values()],
        "relations": [_relation(r) for r in ontology.relations],
    }


def _safe_json(payload: dict[str, object]) -> str:
    """JSON that cannot terminate the surrounding ``<script>`` element."""
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_report(ontology: Ontology, *, title: str = "LoL Coach — Knowledge Graph") -> str:
    """Return the full HTML document for the given ontology."""
    payload = build_payload(ontology, title=title)
    return _TEMPLATE.replace(_JSON_MARKER, _safe_json(payload))


def write_report(
    ontology: Ontology, path: Path, *, title: str = "LoL Coach — Knowledge Graph"
) -> Path:
    """Write the report to ``path`` (creating parent directories)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(ontology, title=title), encoding="utf-8")
    logger.info(
        "Knowledge-graph report written",
        extra={"data": {"path": str(path), "relations": len(ontology.relations)}},
    )
    return path


_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LoL Coach — Knowledge Graph</title>
<style>
  :root {
    --bg: #10131a; --panel: #171b24; --panel-2: #1e2430; --line: #2a3140;
    --text: #dfe3ea; --muted: #8b94a7; --accent: #61afef; --counter: #e06c75;
    --synergy: #98c379; --warn: #e5c07b;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.45 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    background: var(--bg); color: var(--text); }
  header { padding: 16px 22px; border-bottom: 1px solid var(--line); background: var(--panel); }
  h1 { margin: 0 0 4px; font-size: 18px; }
  .meta { color: var(--muted); font-size: 12.5px; }
  .meta b { color: var(--text); font-weight: 600; }
  nav.tabs { margin-top: 12px; display: flex; gap: 6px; }
  nav.tabs button { background: var(--panel-2); color: var(--muted); border: 1px solid var(--line);
    padding: 6px 14px; border-radius: 7px; cursor: pointer; font-size: 13px; }
  nav.tabs button.active { background: var(--accent); color: #0b0e14; border-color: var(--accent); font-weight: 600; }
  main { height: calc(100vh - 118px); }
  section.tab { display: none; height: 100%; }
  section.tab.active { display: flex; }
  /* browse */
  #browse { flex-direction: row; }
  .list { width: 280px; border-right: 1px solid var(--line); display: flex; flex-direction: column; }
  .list input { margin: 12px; padding: 8px 10px; background: var(--panel-2); color: var(--text);
    border: 1px solid var(--line); border-radius: 7px; font-size: 13px; }
  .list .count { padding: 0 12px 8px; color: var(--muted); font-size: 12px; }
  .list ul { list-style: none; margin: 0; padding: 0; overflow-y: auto; flex: 1; }
  .list li { padding: 8px 14px; cursor: pointer; display: flex; justify-content: space-between;
    border-bottom: 1px solid #1a1f29; }
  .list li:hover { background: var(--panel-2); }
  .list li.active { background: #223047; }
  .list li .deg { color: var(--muted); font-size: 12px; }
  .detail { flex: 1; overflow-y: auto; padding: 18px 24px; }
  .detail h2 { margin: 0 0 2px; font-size: 20px; }
  .detail .sub { color: var(--muted); font-size: 12.5px; margin-bottom: 16px; }
  .pill { display: inline-block; background: var(--panel-2); border: 1px solid var(--line);
    border-radius: 999px; padding: 2px 10px; margin: 0 6px 6px 0; font-size: 12px; }
  table { border-collapse: collapse; width: 100%; margin: 6px 0 20px; font-size: 13px; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #1a1f29; vertical-align: top; }
  th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .reason { color: var(--muted); font-size: 12.5px; }
  .h3 { margin: 18px 0 2px; font-size: 13px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); }
  .h3.counter { color: var(--counter); } .h3.synergy { color: var(--synergy); }
  a.lnk { color: var(--accent); cursor: pointer; text-decoration: none; }
  a.lnk:hover { text-decoration: underline; }
  /* graph */
  #graph { position: relative; }
  #graph .controls { position: absolute; top: 12px; left: 12px; z-index: 3; display: flex; flex-wrap: wrap;
    gap: 10px 16px; align-items: center; background: rgba(23,27,36,.92); border: 1px solid var(--line);
    border-radius: 9px; padding: 10px 14px; max-width: calc(100% - 24px); }
  #graph label { color: var(--muted); font-size: 12.5px; display: flex; gap: 6px; align-items: center; }
  #graph select, #graph input[type=range] { background: var(--panel-2); color: var(--text);
    border: 1px solid var(--line); border-radius: 6px; padding: 3px 6px; font-size: 12.5px; }
  #canvas { width: 100%; height: 100%; display: block; cursor: grab; }
  #tooltip { position: absolute; pointer-events: none; background: #0b0e14; border: 1px solid var(--line);
    border-radius: 7px; padding: 6px 10px; font-size: 12.5px; display: none; z-index: 4; }
  #selinfo { position: absolute; right: 12px; top: 12px; z-index: 3; background: rgba(23,27,36,.92);
    border: 1px solid var(--line); border-radius: 9px; padding: 10px 14px; display: none; max-width: 260px; }
  .swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: middle; }
</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <div class="meta" id="meta"></div>
  <nav class="tabs">
    <button data-tab="browse" class="active">Champions</button>
    <button data-tab="graph">Graph</button>
  </nav>
</header>
<main>
  <section id="browse" class="tab active">
    <aside class="list">
      <input id="search" type="search" placeholder="Search champions…" autocomplete="off">
      <div class="count" id="list-count"></div>
      <ul id="champ-list"></ul>
    </aside>
    <div class="detail" id="detail"><p class="reason">Select a champion to inspect its relations.</p></div>
  </section>
  <section id="graph" class="tab">
    <div class="controls">
      <label>Type
        <select id="f-type">
          <option value="both">both</option>
          <option value="counter">counter</option>
          <option value="synergy">synergy</option>
        </select>
      </label>
      <label>Position <select id="f-pos"></select></label>
      <label>Min games <input id="f-games" type="range" min="0" max="5000" step="100" value="200">
        <span id="f-games-v" class="reason"></span></label>
      <span class="reason"><span class="swatch" style="background:var(--counter)"></span>counter
        &nbsp;<span class="swatch" style="background:var(--synergy)"></span>synergy</span>
    </div>
    <canvas id="canvas"></canvas>
    <div id="tooltip"></div>
    <div id="selinfo"></div>
  </section>
</main>
<script id="kg-data" type="application/json">__KG_JSON__</script>
<script>
"use strict";
const DATA = JSON.parse(document.getElementById("kg-data").textContent);
const bySlug = new Map(DATA.champions.map(c => [c.slug, c]));
const nameOf = s => (bySlug.get(s) || {name: s}).name;

// ---- header ----
document.getElementById("title").textContent = DATA.title;
(function () {
  const m = DATA.meta;
  const bits = [
    "patch <b>" + m.patch + "</b>",
    "<b>" + m.champion_count + "</b> champions",
    "<b>" + m.relation_count + "</b> relations",
    "<b>" + m.counter_count + "</b> counters",
    "<b>" + m.synergy_count + "</b> synergies",
    "source <b>" + m.source + "</b>",
    "generated <b>" + m.generated_at + "</b>"
  ];
  document.getElementById("meta").innerHTML = bits.join(" &middot; ");
})();

// ---- indexes ----
const countersOut = new Map(), countersIn = new Map(), synergies = new Map();
function push(map, key, rel) { if (!map.has(key)) map.set(key, []); map.get(key).push(rel); }
for (const r of DATA.relations) {
  if (r.type === "counter") { push(countersOut, r.source, r); push(countersIn, r.target, r); }
  else { push(synergies, r.source, r); if (r.target !== r.source) push(synergies, r.target, r); }
}
const degree = slug => (countersOut.get(slug) || []).length + (countersIn.get(slug) || []).length
  + (synergies.get(slug) || []).length;

// ---- tabs ----
let graphStarted = false;
document.querySelectorAll("nav.tabs button").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("nav.tabs button").forEach(b => b.classList.toggle("active", b === btn));
    document.querySelectorAll("section.tab").forEach(s => s.classList.toggle("active", s.id === btn.dataset.tab));
    if (btn.dataset.tab === "graph") {
      if (!graphStarted) { graphStarted = true; initGraph(); }
    }
  });
});
function showTab(name) {
  document.querySelector('nav.tabs button[data-tab="' + name + '"]').click();
}

// ---- champion browser ----
const listEl = document.getElementById("champ-list");
const detailEl = document.getElementById("detail");
const searchEl = document.getElementById("search");
let activeSlug = null;

function renderList() {
  const q = searchEl.value.trim().toLowerCase();
  const items = DATA.champions
    .filter(c => !q || c.name.toLowerCase().includes(q) || c.slug.includes(q))
    .sort((a, b) => a.name.localeCompare(b.name));
  document.getElementById("list-count").textContent = items.length + " / " + DATA.champions.length;
  listEl.innerHTML = "";
  for (const c of items) {
    const li = document.createElement("li");
    li.className = c.slug === activeSlug ? "active" : "";
    const nm = document.createElement("span"); nm.textContent = c.name;
    const dg = document.createElement("span"); dg.className = "deg"; dg.textContent = degree(c.slug);
    li.append(nm, dg);
    li.addEventListener("click", () => select(c.slug));
    listEl.appendChild(li);
  }
}

function chip(text) { const s = document.createElement("span"); s.className = "pill"; s.textContent = text; return s; }

function relationTable(rows, counterpartOf, showType) {
  const table = document.createElement("table");
  const head = document.createElement("tr");
  for (const h of ["Champion", "Pos", "Win%", "Games", "Weight", "Reason"]) {
    const th = document.createElement("th"); th.textContent = h; head.appendChild(th);
  }
  table.appendChild(head);
  for (const r of rows) {
    const tr = document.createElement("tr");
    const other = counterpartOf(r);
    const tdName = document.createElement("td");
    const link = document.createElement("a"); link.className = "lnk"; link.textContent = nameOf(other);
    link.addEventListener("click", () => select(other));
    tdName.appendChild(link);
    if (showType) { tdName.append(" "); tdName.appendChild(chip(r.type)); }
    const cells = [
      [r.position, ""],
      [(r.win_rate * 100).toFixed(1), "num"],
      [r.games.toLocaleString(), "num"],
      [(r.weight >= 0 ? "+" : "") + r.weight.toFixed(3), "num"],
      [r.reason, "reason"]
    ];
    tr.appendChild(tdName);
    for (const [text, cls] of cells) {
      const td = document.createElement("td"); if (cls) td.className = cls; td.textContent = text; tr.appendChild(td);
    }
    table.appendChild(tr);
  }
  return table;
}

function section(title, cls, rows, counterpartOf, showType) {
  const wrap = document.createElement("div");
  const h = document.createElement("div"); h.className = "h3 " + cls; h.textContent = title + " (" + rows.length + ")";
  wrap.appendChild(h);
  if (!rows.length) { const p = document.createElement("p"); p.className = "reason"; p.textContent = "none"; wrap.appendChild(p); return wrap; }
  const sorted = rows.slice().sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight));
  wrap.appendChild(relationTable(sorted, counterpartOf, showType));
  return wrap;
}

function select(slug) {
  activeSlug = slug;
  renderList();
  const c = bySlug.get(slug);
  detailEl.innerHTML = "";
  if (!c) return;
  const h2 = document.createElement("h2"); h2.textContent = c.name;
  const sub = document.createElement("div"); sub.className = "sub";
  sub.textContent = "slug " + c.slug + " · id " + c.champion_id + " · " + degree(slug) + " relations";
  detailEl.append(h2, sub);
  for (const p of c.positions) {
    const roles = p.roles.length ? " · " + p.roles.join(", ") : "";
    detailEl.appendChild(chip(
      p.position + " — win " + (p.win_rate * 100).toFixed(1) + "% · pick " + (p.pick_rate * 100).toFixed(1) +
      "% · ban " + (p.ban_rate * 100).toFixed(1) + "% · " + p.play.toLocaleString() + " games" + roles));
  }
  detailEl.appendChild(section("Beats (counters)", "counter",
    countersOut.get(slug) || [], r => r.target, false));
  detailEl.appendChild(section("Countered by", "counter",
    countersIn.get(slug) || [], r => r.source, false));
  detailEl.appendChild(section("Synergies", "synergy",
    synergies.get(slug) || [], r => (r.source === slug ? r.target : r.source), false));
  if (activeSlug === slug) listEl.querySelectorAll("li").forEach(li => li.classList.remove("active"));
}
searchEl.addEventListener("input", renderList);

// ---- graph ----
const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");
const tooltip = document.getElementById("tooltip");
const selinfo = document.getElementById("selinfo");
let nodes = [], edges = [], alpha = 1, raf = null, selected = null, hovered = null, drag = null;

function readFilters() {
  return {
    type: document.getElementById("f-type").value,
    pos: document.getElementById("f-pos").value,
    minGames: +document.getElementById("f-games").value
  };
}
function buildGraph() {
  const f = readFilters();
  const W = canvas.clientWidth || 800, H = canvas.clientHeight || 600;
  nodes = DATA.champions.map((c, i) => ({
    slug: c.slug, name: c.name, deg: 0,
    x: W / 2 + Math.cos(i * 2.399) * (60 + Math.sqrt(i) * 22),
    y: H / 2 + Math.sin(i * 2.399) * (60 + Math.sqrt(i) * 22),
    vx: 0, vy: 0
  }));
  const idx = new Map(nodes.map((n, i) => [n.slug, i]));
  edges = [];
  for (const r of DATA.relations) {
    if (f.type !== "both" && r.type !== f.type) continue;
    if (f.pos !== "all" && r.position !== f.pos) continue;
    if (r.games < f.minGames) continue;
    const a = idx.get(r.source), b = idx.get(r.target);
    if (a == null || b == null) continue;
    edges.push({ a, b, type: r.type, weight: r.weight });
    nodes[a].deg++; nodes[b].deg++;
  }
  document.getElementById("selinfo").style.display = "none";
  selected = null; hovered = null; alpha = 1;
  resize();
}
function resize() {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = canvas.clientWidth * dpr;
  canvas.height = canvas.clientHeight * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
function initGraph() {
  const posSel = document.getElementById("f-pos");
  const positions = ["all", ...new Set(DATA.relations.map(r => r.position))].sort(
    (a, b) => a === "all" ? -1 : b === "all" ? 1 : a.localeCompare(b));
  for (const p of positions) { const o = document.createElement("option"); o.value = p; o.textContent = p; posSel.appendChild(o); }
  const games = document.getElementById("f-games");
  games.addEventListener("input", () => {
    document.getElementById("f-games-v").textContent = games.value;
    buildGraph(); reheat();
  });
  games.dispatchEvent(new Event("input"));
  ["f-type", "f-pos"].forEach(id => document.getElementById(id).addEventListener("change", () => { buildGraph(); reheat(); }));
  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointerleave", () => { drag = null; hovered = null; tooltip.style.display = "none"; });
  window.addEventListener("resize", () => { resize(); alpha = Math.max(alpha, 0.3); });
  reheat();
}
function reheat() { alpha = 1; if (raf == null) raf = requestAnimationFrame(loop); }

function nodeAt(px, py) {
  let best = null, bestD = 14 * 14;
  for (const n of nodes) {
    const d = (n.x - px) ** 2 + (n.y - py) ** 2;
    if (d < bestD) { bestD = d; best = n; }
  }
  return best;
}
function pointer(e) {
  const rect = canvas.getBoundingClientRect();
  return { x: e.clientX - rect.left, y: e.clientY - rect.top };
}
function onDown(e) { const p = pointer(e); const n = nodeAt(p.x, p.y); if (n) { drag = n; canvas.style.cursor = "grabbing"; } }
function onMove(e) {
  const p = pointer(e);
  if (drag) { drag.x = p.x; drag.y = p.y; drag.vx = 0; drag.vy = 0; reheat(); return; }
  const n = nodeAt(p.x, p.y);
  hovered = n;
  if (n) {
    tooltip.style.display = "block";
    tooltip.style.left = (p.x + 14) + "px";
    tooltip.style.top = (p.y + 14) + "px";
    tooltip.textContent = n.name + " · " + n.deg + " edges";
    canvas.style.cursor = "pointer";
  } else { tooltip.style.display = "none"; canvas.style.cursor = "grab"; }
}
function onUp(e) {
  if (drag) { drag = null; canvas.style.cursor = "grab"; }
  const p = pointer(e); const n = nodeAt(p.x, p.y);
  if (n) {
    selected = n;
    selinfo.style.display = "block";
    selinfo.innerHTML = "";
    const t = document.createElement("div"); t.innerHTML = "<b>" + n.name + "</b> · " + n.deg + " edges";
    const b = document.createElement("button"); b.textContent = "Open in Champions"; b.className = "lnk";
    b.style.cssText = "margin-top:8px;background:none;border:1px solid var(--line);border-radius:6px;padding:4px 8px;color:var(--accent);cursor:pointer";
    b.addEventListener("click", () => { select(n.slug); showTab("browse"); });
    selinfo.append(t, b);
  } else { selected = null; selinfo.style.display = "none"; }
}

function loop() {
  raf = null;
  if (alpha > 0.004) { step(); draw(); raf = requestAnimationFrame(loop); }
  else draw();
}
function step() {
  const W = canvas.clientWidth, H = canvas.clientHeight, n = nodes.length || 1;
  const spacing = Math.sqrt((W * H) / n) || 40;
  const rep = spacing * spacing * 1.15, rest = spacing * 0.85, spring = 0.045, gravity = 0.015, damp = 0.86;
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
      if (d2 < 1) { dx = (Math.random() - 0.5); dy = (Math.random() - 0.5); d2 = 1; }
      const d = Math.sqrt(d2), f = rep / d2;
      const fx = (dx / d) * f, fy = (dy / d) * f;
      a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
    }
  }
  for (const e of edges) {
    const a = nodes[e.a], b = nodes[e.b];
    let dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1;
    const f = (d - rest) * spring, fx = (dx / d) * f, fy = (dy / d) * f;
    a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
  }
  for (const nd of nodes) {
    nd.vx += (W / 2 - nd.x) * gravity; nd.vy += (H / 2 - nd.y) * gravity;
    nd.vx *= damp; nd.vy *= damp;
    nd.x += nd.vx * alpha; nd.y += nd.vy * alpha;
    nd.x = Math.max(14, Math.min(W - 14, nd.x)); nd.y = Math.max(14, Math.min(H - 14, nd.y));
  }
  alpha *= 0.985;
}
function draw() {
  const W = canvas.clientWidth, H = canvas.clientHeight;
  ctx.clearRect(0, 0, W, H);
  const near = new Set();
  if (selected) for (const e of edges) { if (nodes[e.a] === selected) near.add(nodes[e.b]); if (nodes[e.b] === selected) near.add(nodes[e.a]); }
  for (const e of edges) {
    const a = nodes[e.a], b = nodes[e.b];
    const lit = selected && (a === selected || b === selected);
    ctx.strokeStyle = e.type === "counter"
      ? (lit ? "rgba(224,108,117,.85)" : "rgba(224,108,117,.16)")
      : (lit ? "rgba(152,195,121,.85)" : "rgba(152,195,121,.16)");
    ctx.lineWidth = lit ? 1.6 : 0.7;
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  }
  const maxDeg = Math.max(1, ...nodes.map(n => n.deg));
  for (const n of nodes) {
    const r = 3 + Math.sqrt(n.deg / maxDeg) * 9;
    const isSel = n === selected, isHov = n === hovered, isNear = near.has(n);
    ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
    ctx.fillStyle = isSel ? "#e5c07b" : isHov ? "#98c379" : isNear ? "#61afef" : "#3d4a63";
    ctx.fill();
    if (isSel || isHov) { ctx.lineWidth = 1.5; ctx.strokeStyle = "#dfe3ea"; ctx.stroke(); }
    if (r > 7 || isSel || isHov || isNear) {
      ctx.fillStyle = isSel || isHov ? "#dfe3ea" : "rgba(223,227,234,.65)";
      ctx.font = "11px system-ui, sans-serif";
      ctx.fillText(n.name, n.x + r + 3, n.y + 3);
    }
  }
}
</script>
</body>
</html>
"""
