import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import type { ExecutionDetail, ExecutionSummary } from "../types";
import { StatusBadge } from "./Badges";
import TraceGraph from "./TraceGraph";

interface Props {
  selectedId: string | null;
  onSelect: (id: string) => void;
  refreshKey: number;
}

function durationOf(row: ExecutionSummary): string {
  if (row.finished_at === null) return "—";
  const ms = new Date(row.finished_at).getTime() - new Date(row.started_at).getTime();
  return `${(ms / 1000).toFixed(1)}s`;
}

function ExecutionList({
  rows,
  selectedId,
  onSelect,
}: {
  rows: ExecutionSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (rows.length === 0) {
    return <p className="px-1 text-sm text-slate-500">No executions match. Evaluate a draft.</p>;
  }
  return (
    <ul className="space-y-1.5">
      {rows.map((row) => (
        <li key={row.execution_id}>
          <button
            type="button"
            onClick={() => onSelect(row.execution_id)}
            className={`w-full rounded-lg border px-3 py-2 text-left transition ${
              row.execution_id === selectedId
                ? "border-cyan-500/70 bg-cyan-500/10"
                : "border-edge bg-panel/50 hover:border-slate-500"
            }`}
          >
            <div className="mb-1 flex items-center justify-between gap-2">
              <span className="truncate font-mono text-xs text-slate-300">
                {row.execution_id.slice(0, 12)}
              </span>
              <StatusBadge status={row.status} />
            </div>
            <div className="flex items-center justify-between text-[11px] text-slate-500">
              <span className="truncate">
                {row.workflow} · {row.started_at.replace("T", " ").slice(0, 19)}
              </span>
              <span className="ml-2 shrink-0 font-mono">{durationOf(row)}</span>
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}

export default function TraceInspector({ selectedId, onSelect, refreshKey }: Props) {
  const [summaries, setSummaries] = useState<ExecutionSummary[]>([]);
  const [detail, setDetail] = useState<ExecutionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [workflowFilter, setWorkflowFilter] = useState("all");
  const [tick, setTick] = useState(0);
  const [rerunning, setRerunning] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api
      .executions()
      .then((rows) => {
        if (!alive) return;
        setSummaries(rows);
        setError(null);
        if (selectedId === null && rows.length > 0) onSelect(rows[0].execution_id);
      })
      .catch((cause: unknown) => {
        if (alive) setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [refreshKey, tick]);

  useEffect(() => {
    if (selectedId === null) {
      setDetail(null);
      return;
    }
    let alive = true;
    api
      .execution(selectedId)
      .then((record) => {
        if (alive) setDetail(record);
      })
      .catch((cause: unknown) => {
        if (alive) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      alive = false;
    };
  }, [selectedId, refreshKey]);

  const workflows = useMemo(
    () => Array.from(new Set(summaries.map((row) => row.workflow))).sort(),
    [summaries]
  );

  const onRerun = useCallback(() => {
    if (selectedId === null || rerunning) return;
    setRerunning(true);
    api
      .rerun(selectedId)
      .then((response) => {
        setTick((value) => value + 1);
        onSelect(response.execution_id);
      })
      .catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => setRerunning(false));
  }, [selectedId, rerunning, onSelect]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return summaries.filter((row) => {
      if (statusFilter !== "all" && row.status !== statusFilter) return false;
      if (workflowFilter !== "all" && row.workflow !== workflowFilter) return false;
      if (needle && !`${row.execution_id} ${row.workflow}`.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [summaries, query, statusFilter, workflowFilter]);

  const selectClass =
    "rounded border border-edge bg-panel/60 px-2 py-1 text-xs text-slate-300 outline-none focus:border-cyan-500/70";

  return (
    <div className="grid h-[calc(100vh-7rem)] min-h-[32rem] gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
      <aside className="flex min-h-0 flex-col rounded-lg border border-edge bg-panel/60 p-3">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-200">Executions</h2>
          {loading && <span className="text-xs text-slate-500">loading…</span>}
        </div>
        <div className="mb-3 space-y-2">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search executions…"
            className="w-full rounded border border-edge bg-ink/60 px-2 py-1.5 text-xs text-slate-200 outline-none placeholder:text-slate-600 focus:border-cyan-500/70"
          />
          <div className="flex gap-2">
            <select
              value={workflowFilter}
              onChange={(event) => setWorkflowFilter(event.target.value)}
              className={`${selectClass} flex-1`}
            >
              <option value="all">All Workflows</option>
              {workflows.map((workflow) => (
                <option key={workflow} value={workflow}>
                  {workflow}
                </option>
              ))}
            </select>
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              className={`${selectClass} flex-1`}
            >
              <option value="all">All Status</option>
              <option value="succeeded">Success</option>
              <option value="failed">Failed</option>
            </select>
          </div>
        </div>
        {error !== null && <p className="mb-2 text-xs text-rose-300">{error}</p>}
        <div className="min-h-0 flex-1 overflow-auto pr-1">
          <ExecutionList rows={filtered} selectedId={selectedId} onSelect={onSelect} />
        </div>
      </aside>

      <section className="flex min-h-0 flex-col">
        {detail === null ? (
          <div className="flex h-full items-center justify-center rounded-lg border border-edge bg-[#0c1322]">
            <p className="text-sm text-slate-500">Select an execution to inspect its timeline.</p>
          </div>
        ) : (
          <>
            <div className="mb-2 flex items-center gap-2 px-1 text-xs text-slate-500">
              <span>Executions</span>
              <span>/</span>
              <span className="font-mono text-slate-400">{detail.execution_id}</span>
            </div>
            <div className="min-h-0 flex-1">
              <TraceGraph detail={detail} onRerun={onRerun} rerunning={rerunning} />
            </div>
          </>
        )}
      </section>
    </div>
  );
}
