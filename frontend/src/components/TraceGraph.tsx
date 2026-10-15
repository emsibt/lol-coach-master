import { useEffect, useMemo, useState } from "react";

import type { EventRecord, ExecutionDetail, LogRecord, WorkflowGraph } from "../types";
import { StatusBadge } from "./Badges";

type NodeKind = "start" | "task" | "choice" | "succeed" | "fail";
const START = "__start__";

interface Occ {
  entered: EventRecord | null;
  outcome: EventRecord | null;
  exec: EventRecord | null;
}

interface GNode {
  id: string;
  label: string;
  kind: NodeKind;
  task: string | null;
  error: string | null;
  occs: Occ[];
  layer: number;
  row: number;
  x: number;
  y: number;
}

interface GEdge {
  from: string;
  to: string;
  label: string | null;
  back: boolean;
  taken: boolean;
}

interface Graph {
  nodes: GNode[];
  byId: Map<string, GNode>;
  edges: GEdge[];
  width: number;
  height: number;
}

const NODE_W = 216;
const NODE_H = 98;
const GAP_X = 104;
const GAP_Y = 46;
const PAD_X = 48;
const PAD_Y = 64;

// ---- occurrences ----------------------------------------------------------

function buildOccurrences(events: EventRecord[]): { start: Occ; byState: Map<string, Occ[]> } {
  const start: Occ = { entered: null, outcome: null, exec: null };
  const byState = new Map<string, Occ[]>();
  const open = new Map<string, Occ>();
  let last: Occ | null = null;

  const record = (state: string, occ: Occ): Occ => {
    const list = byState.get(state) ?? [];
    list.push(occ);
    byState.set(state, list);
    return occ;
  };

  for (const event of events) {
    if (event.type === "EXECUTION_STARTED") {
      start.entered = event;
    } else if (event.type === "STATE_ENTERED") {
      const occ: Occ = { entered: event, outcome: null, exec: null };
      last = record(event.state_name ?? "?", occ);
      open.set(event.state_name ?? "?", occ);
    } else if (event.type === "STATE_SUCCEEDED" || event.type === "STATE_FAILED") {
      const state = event.state_name;
      const occ = state !== null ? open.get(state) : undefined;
      if (occ !== undefined && state !== null) {
        occ.outcome = event;
        open.delete(state);
      } else {
        last = record(state ?? "?", { entered: null, outcome: event, exec: null });
      }
    } else if (event.type === "EXECUTION_SUCCEEDED" || event.type === "EXECUTION_FAILED") {
      if (last !== null) last.exec = event;
    }
  }
  return { start, byState };
}

// ---- graph construction ---------------------------------------------------

function kindOf(type: string | undefined): NodeKind {
  if (type === "choice") return "choice";
  if (type === "succeed") return "succeed";
  if (type === "fail") return "fail";
  return "task";
}

interface RawEdge {
  from: string;
  to: string;
  label: string | null;
}

function topologyEdges(graph: WorkflowGraph): RawEdge[] {
  const edges: RawEdge[] = [{ from: START, to: graph.start_at, label: null }];
  for (const state of graph.states) {
    if (state.type === "task" && state.next !== null) {
      edges.push({ from: state.name, to: state.next, label: null });
    } else if (state.type === "choice") {
      for (const rule of state.choices) {
        edges.push({ from: state.name, to: rule.next, label: rule.predicate });
      }
      if (state.default !== null) {
        edges.push({ from: state.name, to: state.default, label: "default" });
      }
    }
  }
  return edges;
}

// Fallback when the backend has no topology (e.g. an older execution).
function inferNodes(byState: Map<string, Occ[]>): GNode[] {
  const nodes: GNode[] = [];
  for (const [state, occs] of byState) {
    const kind: NodeKind = state === "Succeeded" ? "succeed" : state === "Failed" ? "fail" : "task";
    nodes.push(makeNode(state, state, kind, null, null, occs));
  }
  return nodes;
}

function makeNode(
  id: string,
  label: string,
  kind: NodeKind,
  task: string | null,
  error: string | null,
  occs: Occ[]
): GNode {
  return { id, label, kind, task, error, occs, layer: 0, row: 0, x: 0, y: 0 };
}

function buildGraph(detail: ExecutionDetail): Graph {
  const { start, byState } = buildOccurrences(detail.events);
  const topology = detail.graph;

  const nodes: GNode[] = [makeNode(START, "EXECUTION_STARTED", "start", null, null, [start])];
  if (topology !== null) {
    for (const state of topology.states) {
      nodes.push(
        makeNode(
          state.name,
          state.name,
          kindOf(state.type),
          state.task,
          state.error,
          byState.get(state.name) ?? []
        )
      );
    }
  } else {
    nodes.push(...inferNodes(byState));
  }
  const byId = new Map(nodes.map((node) => [node.id, node]));

  // Executed transition sequence, in event order.
  const sequence: string[] = [START];
  for (const event of detail.events) {
    if (event.type === "STATE_ENTERED") sequence.push(event.state_name ?? "?");
    else if (
      (event.type === "STATE_SUCCEEDED" || event.type === "STATE_FAILED") &&
      event.state_name !== null &&
      !sequence.slice(1).includes(event.state_name)
    ) {
      sequence.push(event.state_name);
    }
  }
  const taken = new Set<string>();
  for (let i = 1; i < sequence.length; i += 1) taken.add(`${sequence[i - 1]}->${sequence[i]}`);

  const rawEdges: RawEdge[] =
    topology !== null
      ? topologyEdges(topology)
      : sequence.slice(1).reduce<RawEdge[]>((acc, to, i) => {
          acc.push({ from: sequence[i], to, label: null });
          return acc;
        }, []);

  // Deduplicate while preserving order.
  const seen = new Set<string>();
  const edges: GEdge[] = [];
  for (const raw of rawEdges) {
    const key = `${raw.from}->${raw.to}`;
    if (seen.has(key)) continue;
    seen.add(key);
    edges.push({ ...raw, back: false, taken: taken.has(key) });
  }

  // Depth-first discovery to find back edges (loops like RunTool -> Think).
  const adjacency = new Map<string, GEdge[]>();
  for (const edge of edges) {
    const list = adjacency.get(edge.from) ?? [];
    list.push(edge);
    adjacency.set(edge.from, list);
  }
  const order: string[] = [];
  const index = new Map<string, number>();
  const onStack = new Set<string>();
  const backKeys = new Set<string>();
  const visit = (id: string) => {
    index.set(id, order.length);
    order.push(id);
    onStack.add(id);
    for (const edge of adjacency.get(id) ?? []) {
      if (onStack.has(edge.to)) backKeys.add(`${edge.from}->${edge.to}`);
      else if (!index.has(edge.to)) visit(edge.to);
    }
    onStack.delete(id);
  };
  visit(START);
  // Any node not reachable from the start (defensive): append in definition order.
  for (const node of nodes) if (!index.has(node.id)) visit(node.id);

  for (const edge of edges) {
    edge.back = backKeys.has(`${edge.from}->${edge.to}`);
  }

  // Longest-path layering over forward edges, in discovery order.
  for (const id of order) {
    const node = byId.get(id);
    if (node === undefined) continue;
    for (const edge of adjacency.get(id) ?? []) {
      if (edge.back) continue;
      const target = byId.get(edge.to);
      if (target !== undefined && (index.get(edge.to) ?? 0) > (index.get(id) ?? 0)) {
        target.layer = Math.max(target.layer, node.layer + 1);
      }
    }
  }

  const columns = new Map<number, GNode[]>();
  for (const id of order) {
    const node = byId.get(id);
    if (node === undefined) continue;
    const column = columns.get(node.layer) ?? [];
    column.push(node);
    columns.set(node.layer, column);
  }
  let maxX = 0;
  let maxY = 0;
  for (const [layer, column] of columns) {
    column.sort((a, b) => (index.get(a.id) ?? 0) - (index.get(b.id) ?? 0));
    column.forEach((node, row) => {
      node.row = row;
      node.x = PAD_X + layer * (NODE_W + GAP_X);
      node.y = PAD_Y + row * (NODE_H + GAP_Y);
      maxX = Math.max(maxX, node.x + NODE_W);
      maxY = Math.max(maxY, node.y + NODE_H);
    });
  }

  return { nodes, byId, edges, width: maxX + PAD_X, height: maxY + PAD_Y };
}

// ---- node helpers ---------------------------------------------------------

function nodeEvents(node: GNode): EventRecord[] {
  const out: EventRecord[] = [];
  for (const occ of node.occs) {
    if (occ.entered !== null) out.push(occ.entered);
    if (occ.outcome !== null) out.push(occ.outcome);
    if (occ.exec !== null) out.push(occ.exec);
  }
  return out;
}

function nodeLogs(node: GNode): LogRecord[] {
  return nodeEvents(node).flatMap((event) => event.logs);
}

function nodeStatus(node: GNode): string {
  const events = nodeEvents(node);
  if (events.some((event) => event.type.includes("FAILED"))) return "failed";
  if (events.some((event) => event.type.includes("SUCCEEDED"))) return "succeeded";
  return node.occs.length > 0 ? "running" : "not_run";
}

function nodeDuration(node: GNode): number {
  return node.occs.reduce((total, occ) => total + (occ.outcome?.duration_ms ?? 0), 0);
}

// ---- icons ----------------------------------------------------------------

function Icon({ name }: { name: NodeKind | "ai" | "gear" }) {
  const common = {
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  switch (name) {
    case "start":
      return (
        <svg viewBox="0 0 24 24" className="h-4 w-4" {...common}>
          <path d="M7 5l11 7-11 7V5z" />
        </svg>
      );
    case "ai":
      return (
        <svg viewBox="0 0 24 24" className="h-4 w-4" {...common}>
          <path d="M12 3v3M12 18v3M3 12h3M18 12h3M6 6l2 2M16 16l2 2M18 6l-2 2M8 16l-2 2" />
          <circle cx="12" cy="12" r="3.4" />
        </svg>
      );
    case "choice":
      return (
        <svg viewBox="0 0 24 24" className="h-4 w-4" {...common}>
          <circle cx="6" cy="6" r="2.2" />
          <circle cx="6" cy="18" r="2.2" />
          <circle cx="18" cy="12" r="2.2" />
          <path d="M8 6h2a2 2 0 012 2v0a4 4 0 01-4 4H8M8 18h2a2 2 0 002-2v0" />
        </svg>
      );
    case "succeed":
      return (
        <svg viewBox="0 0 24 24" className="h-4 w-4" {...common}>
          <path d="M5 12.5l4.5 4.5L19 7" />
        </svg>
      );
    case "fail":
      return (
        <svg viewBox="0 0 24 24" className="h-4 w-4" {...common}>
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      );
    default:
      return (
        <svg viewBox="0 0 24 24" className="h-4 w-4" {...common}>
          <circle cx="12" cy="12" r="3.2" />
          <path d="M12 4v2M12 18v2M4 12h2M18 12h2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M17.7 6.3l-1.4 1.4M7.7 16.3l-1.4 1.4" />
        </svg>
      );
  }
}

function iconName(node: GNode): NodeKind | "ai" | "gear" {
  if (node.kind === "task" && /think/i.test(node.label)) return "ai";
  if (node.kind === "task") return "gear";
  return node.kind;
}

function iconTone(node: GNode): string {
  const status = nodeStatus(node);
  if (status === "not_run") return "border-slate-600 bg-slate-700/30 text-slate-500";
  if (status === "failed" || node.kind === "fail") return "border-rose-500/50 bg-rose-500/15 text-rose-300";
  if (node.kind === "start") return "border-sky-500/50 bg-sky-500/15 text-sky-300";
  if (node.kind === "choice") return "border-amber-500/50 bg-amber-500/15 text-amber-300";
  if (node.kind === "succeed") return "border-emerald-500/50 bg-emerald-500/15 text-emerald-300";
  if (/think/i.test(node.label)) return "border-violet-500/50 bg-violet-500/15 text-violet-300";
  return "border-cyan-500/50 bg-cyan-500/15 text-cyan-300";
}

// ---- JSON view ------------------------------------------------------------

function JsonView({ value }: { value: unknown }) {
  const text = value === undefined ? "null" : JSON.stringify(value, null, 2);
  if (text === undefined) return <pre className="p-3 font-mono text-[12px] text-slate-500">null</pre>;
  const tokens = text.split(/("(?:\\.|[^"\\])*"\s*:?|\b-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b|\btrue\b|\bfalse\b|\bnull\b)/g);
  return (
    <pre className="overflow-auto p-3 font-mono text-[12px] leading-relaxed text-slate-300">
      {tokens.map((token, i) => {
        let className = "";
        if (/^".*":$/.test(token)) className = "text-sky-300";
        else if (/^"/.test(token)) className = "text-emerald-300";
        else if (/^(true|false|null)$/.test(token)) className = "text-violet-300";
        else if (/^-?\d/.test(token)) className = "text-amber-300";
        return (
          <span key={i} className={className}>
            {token}
          </span>
        );
      })}
    </pre>
  );
}

// ---- edges ----------------------------------------------------------------

function edgePath(edge: GEdge, graph: Graph): string {
  const from = graph.byId.get(edge.from);
  const to = graph.byId.get(edge.to);
  if (from === undefined || to === undefined) return "";
  if (edge.back) {
    const sx = from.x + NODE_W / 2;
    const sy = from.y + NODE_H;
    const tx = to.x + NODE_W / 2;
    const ty = to.y;
    const dip = Math.max(sy, ty) + 46;
    return `M ${sx} ${sy} C ${sx} ${dip}, ${tx} ${dip}, ${tx} ${ty}`;
  }
  const sx = from.x + NODE_W;
  const sy = from.y + NODE_H / 2;
  const tx = to.x;
  const ty = to.y + NODE_H / 2;
  const mid = sx + (tx - sx) / 2;
  return `M ${sx} ${sy} C ${mid} ${sy}, ${mid} ${ty}, ${tx} ${ty}`;
}

function edgeColor(edge: GEdge, graph: Graph): string {
  if (!edge.taken) return "#3a4a6b";
  const to = graph.byId.get(edge.to);
  if (to !== undefined && nodeStatus(to) === "failed") return "#fb7185";
  return "#34d399";
}

function edgeLabelPos(edge: GEdge, graph: Graph): { x: number; y: number } | null {
  const from = graph.byId.get(edge.from);
  const to = graph.byId.get(edge.to);
  if (from === undefined || to === undefined) return null;
  return {
    x: from.x + NODE_W + (to.x - (from.x + NODE_W)) / 2,
    y: from.y + NODE_H / 2 + (to.y - from.y) / 2 - 6,
  };
}

// ---- node card ------------------------------------------------------------

function TraceNodeCard({
  node,
  selected,
  onSelect,
}: {
  node: GNode;
  selected: boolean;
  onSelect: () => void;
}) {
  const status = nodeStatus(node);
  const logs = nodeLogs(node);
  const duration = nodeDuration(node);
  const unrun = status === "not_run";
  const failed = status === "failed";
  const border = selected
    ? "border-cyan-400 shadow-[0_0_0_1px_rgba(34,211,238,0.6)]"
    : unrun
      ? "border-edge/70"
      : failed
        ? "border-rose-500/60 hover:border-rose-400"
        : "border-edge hover:border-slate-500";
  return (
    <button
      type="button"
      onClick={onSelect}
      style={{ left: node.x, top: node.y, width: NODE_W, height: NODE_H }}
      className={`absolute flex flex-col justify-between rounded-lg border bg-panel/90 p-2.5 text-left shadow-lg transition ${
        unrun ? "opacity-55" : ""
      } ${border}`}
    >
      <div className="flex items-center gap-2">
        <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md border ${iconTone(node)}`}>
          <Icon name={iconName(node)} />
        </span>
        <span className="truncate text-sm font-semibold text-slate-100">{node.label}</span>
        {node.occs.length > 1 && (
          <span className="ml-auto shrink-0 rounded bg-slate-700/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
            ×{node.occs.length}
          </span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {unrun ? (
          <span className="rounded border border-slate-600/50 bg-slate-700/20 px-1.5 py-0.5 font-mono text-[9px] font-semibold uppercase text-slate-500">
            not run
          </span>
        ) : (
          <span
            className={`rounded border px-1.5 py-0.5 font-mono text-[9px] font-semibold uppercase ${
              failed
                ? "border-rose-500/40 bg-rose-500/10 text-rose-300"
                : "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
            }`}
          >
            {failed ? "failed" : "succeeded"}
          </span>
        )}
        {node.kind === "choice" && node.occs.length > 0 && (
          <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 font-mono text-[9px] font-semibold uppercase text-amber-300">
            choice
          </span>
        )}
        {!unrun && <span className="ml-auto font-mono text-[10px] text-slate-500">{duration} ms</span>}
      </div>
      {logs.length > 0 && (
        <span className="absolute -right-2 -top-2 rounded-full border border-cyan-400/60 bg-cyan-500/20 px-1.5 py-0.5 font-mono text-[9px] font-semibold text-cyan-200">
          {logs.length}
        </span>
      )}
    </button>
  );
}

// ---- detail panel ---------------------------------------------------------

function LogLine({ log }: { log: LogRecord }) {
  return (
    <li className="border-b border-edge/40 px-3 py-2 last:border-b-0">
      <div className="flex items-center gap-2">
        <span className="rounded border border-slate-500/40 bg-slate-500/10 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-slate-300">
          {log.level}
        </span>
        <span className="truncate font-mono text-[11px] text-slate-500">{log.logger}</span>
        <span className="ml-auto shrink-0 font-mono text-[10px] text-slate-600">{log.timestamp.slice(11, 23)}</span>
      </div>
      <p className="mt-1 whitespace-pre-wrap break-words text-[12px] text-slate-200">{log.message}</p>
      {log.data != null && Object.keys(log.data).length > 0 && (
        <pre className="mt-1 overflow-auto rounded border border-edge bg-ink/60 p-2 font-mono text-[11px] text-slate-400">
          {JSON.stringify(log.data, null, 2)}
        </pre>
      )}
      {log.exception != null && (
        <pre className="mt-1 overflow-auto rounded border border-rose-500/40 bg-rose-950/30 p-2 font-mono text-[11px] leading-relaxed text-rose-200">
          {log.exception}
        </pre>
      )}
    </li>
  );
}

type PanelTab = "input" | "output" | "logs";

function DetailPanel({ node, onClose }: { node: GNode; onClose: () => void }) {
  const logs = nodeLogs(node);
  const [tab, setTab] = useState<PanelTab>("output");
  const [occIndex, setOccIndex] = useState(0);

  useEffect(() => {
    setOccIndex(0);
    setTab(logs.length > 0 ? "logs" : "output");
  }, [node.id, logs.length]);

  const occ = node.occs[Math.min(occIndex, Math.max(node.occs.length - 1, 0))];
  const input = occ?.entered?.input ?? null;
  const output = occ?.outcome?.output ?? occ?.exec?.output ?? null;
  const error = occ?.outcome?.error ?? occ?.exec?.error ?? null;
  const traceback = occ?.outcome?.traceback ?? null;

  return (
    <div className="absolute bottom-4 right-4 z-20 flex max-h-[64%] w-[560px] flex-col overflow-hidden rounded-lg border border-edge bg-[#0d1424] shadow-2xl">
      <div className="flex items-center gap-2 border-b border-edge px-3 py-2">
        <span className="text-sm font-semibold text-slate-200">{node.label}</span>
        <StatusBadge status={nodeStatus(node)} />
        {node.occs.length > 1 && (
          <span className="flex gap-1">
            {node.occs.map((_, i) => (
              <button
                key={i}
                type="button"
                onClick={() => setOccIndex(i)}
                className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${
                  i === occIndex ? "bg-cyan-500/20 text-cyan-200" : "text-slate-500 hover:text-slate-300"
                }`}
              >
                #{i + 1}
              </button>
            ))}
          </span>
        )}
        <button
          type="button"
          onClick={onClose}
          className="ml-auto rounded px-1.5 text-slate-400 hover:text-slate-100"
          aria-label="Close"
        >
          ✕
        </button>
      </div>
      <div className="flex gap-1 border-b border-edge px-2">
        {(["input", "output", "logs"] as const).map((name) => (
          <button
            key={name}
            type="button"
            onClick={() => setTab(name)}
            className={`-mb-px border-b-2 px-3 py-2 text-xs font-medium capitalize transition ${
              tab === name ? "border-cyan-400 text-cyan-200" : "border-transparent text-slate-500 hover:text-slate-300"
            }`}
          >
            {name}
            {name === "logs" && logs.length > 0 ? ` (${logs.length})` : ""}
          </button>
        ))}
      </div>
      <div className="min-h-[160px] flex-1 overflow-auto">
        {occ === undefined ? (
          <p className="p-3 text-sm text-slate-500">This state did not run in this execution.</p>
        ) : (
          <>
            {tab === "input" && <JsonView value={input} />}
            {tab === "output" && (
              <div>
                <JsonView value={output} />
                {error !== null && (
                  <pre className="border-t border-rose-500/30 p-3 font-mono text-[12px] text-rose-300">{error}</pre>
                )}
                {traceback !== null && (
                  <pre className="overflow-auto border-t border-rose-500/30 bg-rose-950/30 p-3 font-mono text-[11px] leading-relaxed text-rose-200">
                    {traceback}
                  </pre>
                )}
              </div>
            )}
            {tab === "logs" &&
              (logs.length === 0 ? (
                <p className="p-3 text-sm text-slate-500">No logs were emitted for this state.</p>
              ) : (
                <ul>
                  {logs.map((log, i) => (
                    <LogLine key={i} log={log} />
                  ))}
                </ul>
              ))}
          </>
        )}
      </div>
    </div>
  );
}

// ---- public component -----------------------------------------------------

function durationLabel(detail: ExecutionDetail): string {
  if (detail.finished_at === null) return "—";
  const ms = new Date(detail.finished_at).getTime() - new Date(detail.started_at).getTime();
  return `${(ms / 1000).toFixed(1)}s`;
}

export default function TraceGraph({
  detail,
  onRerun,
  rerunning,
}: {
  detail: ExecutionDetail;
  onRerun: () => void;
  rerunning: boolean;
}) {
  const graph = useMemo(() => buildGraph(detail), [detail]);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    setSelected(null);
  }, [detail.execution_id]);

  const selectedNode = selected !== null ? graph.byId.get(selected) ?? null : null;
  const lastRun = [...graph.nodes].reverse().find((node) => nodeStatus(node) !== "not_run")?.id ?? null;

  return (
    <div className="flex h-full min-h-0 flex-col rounded-lg border border-edge bg-[#0c1322]">
      <div className="flex flex-wrap items-center gap-3 border-b border-edge px-4 py-3">
        <h2 className="text-base font-semibold text-slate-100">Timeline</h2>
        <StatusBadge status={detail.status} />
        <span className="text-xs text-slate-500">
          {detail.events.length} events · {detail.started_at.replace("T", " ").slice(0, 19)} · Duration:{" "}
          {durationLabel(detail)}
        </span>
        <button
          type="button"
          onClick={onRerun}
          disabled={rerunning}
          className="ml-auto flex items-center gap-1.5 rounded border border-edge bg-panel/60 px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:border-cyan-500/70 hover:text-cyan-200 disabled:opacity-50"
        >
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 12a9 9 0 11-3-6.7M21 4v4h-4" />
          </svg>
          {rerunning ? "Running…" : "Rerun"}
        </button>
      </div>
      <div className="relative min-h-0 flex-1 overflow-auto">
        <div className="relative" style={{ width: graph.width, height: graph.height }}>
          <svg className="absolute inset-0" width={graph.width} height={graph.height}>
            <defs>
              <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M0 0L10 5L0 10z" fill="#3a4a6b" />
              </marker>
            </defs>
            {graph.edges.map((edge) => {
              const d = edgePath(edge, graph);
              if (!d) return null;
              const label = edgeLabelPos(edge, graph);
              return (
                <g key={`${edge.from}->${edge.to}`}>
                  <path
                    d={d}
                    fill="none"
                    stroke={edgeColor(edge, graph)}
                    strokeWidth={edge.taken ? 2 : 1.5}
                    strokeDasharray={edge.taken ? undefined : "5 4"}
                    opacity={edge.taken ? 0.85 : 0.5}
                  />
                  {edge.label !== null && label !== null && (
                    <text
                      x={label.x}
                      y={label.y}
                      textAnchor="middle"
                      className="fill-slate-500 font-mono text-[9px]"
                      style={{ fontSize: 9 }}
                    >
                      {edge.label}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
          {graph.nodes.map((node) => (
            <TraceNodeCard
              key={node.id}
              node={node}
              selected={selected === node.id || (selected === null && node.id === lastRun)}
              onSelect={() => setSelected(node.id)}
            />
          ))}
        </div>
        {selectedNode !== null && <DetailPanel node={selectedNode} onClose={() => setSelected(null)} />}
      </div>
    </div>
  );
}
