import { useEffect, useMemo, useState } from "react";

import { api } from "../api";
import type { EvalRunDetail, EvalRunSummary, EvalScenario, ExecutionDetail } from "../types";
import { StatusBadge } from "./Badges";
import TraceGraph from "./TraceGraph";

interface Props {
  refreshKey: number;
}

function RunCard({
  run,
  active,
  onSelect,
}: {
  run: EvalRunSummary;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full rounded-lg border px-3 py-2 text-left transition ${
        active ? "border-cyan-500/70 bg-cyan-500/10" : "border-edge bg-panel/50 hover:border-slate-500"
      }`}
    >
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-slate-300">{run.kind}</span>
        <StatusBadge status={run.ok ? "succeeded" : "failed"} />
      </div>
      <div className="flex items-center justify-between text-[11px] text-slate-500">
        <span className="truncate">{run.started_at.replace("T", " ").slice(0, 19)}</span>
        <span className={`ml-2 shrink-0 font-mono ${run.ok ? "text-emerald-300" : "text-rose-300"}`}>
          {run.passed}/{run.total}
        </span>
      </div>
      <div className="mt-1 truncate font-mono text-[10px] text-slate-600">
        {run.model === "" ? run.provider : `${run.provider}/${run.model}`}
      </div>
    </button>
  );
}

function CheckRow({
  scenario,
  active,
  onOpenTrace,
}: {
  scenario: EvalScenario;
  active: boolean;
  onOpenTrace: () => void;
}) {
  return (
    <li className="px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className={scenario.passed ? "text-emerald-300" : "text-rose-300"}>
          {scenario.passed ? "[x]" : "[ ]"}
        </span>
        <span className="text-sm text-slate-200">{scenario.name}</span>
        <span className="font-mono text-[11px] text-slate-500">{scenario.id}</span>
        {scenario.kind !== "" && (
          <span className="rounded border border-slate-500/40 bg-slate-500/10 px-1.5 py-0.5 text-[10px] uppercase text-slate-400">
            {scenario.kind}
          </span>
        )}
        {scenario.mode !== "" && <span className="text-[11px] text-slate-500">mode {scenario.mode}</span>}
        {scenario.status !== "" && <StatusBadge status={scenario.status} />}
        <button
          type="button"
          onClick={onOpenTrace}
          disabled={scenario.execution_id === ""}
          className={`ml-auto rounded border px-2 py-1 text-[11px] font-medium transition disabled:cursor-not-allowed disabled:opacity-40 ${
            active
              ? "border-cyan-500/70 bg-cyan-500/10 text-cyan-200"
              : "border-edge bg-panel/60 text-slate-300 hover:border-cyan-500/70 hover:text-cyan-200"
          }`}
        >
          {active ? "Trace shown" : "Show trace"}
        </button>
      </div>
      {scenario.error !== null && scenario.error !== "" && (
        <p className="mt-1 pl-5 text-xs text-rose-300">{scenario.error}</p>
      )}
      <ul className="mt-1 space-y-0.5 pl-5">
        {scenario.checks.map((check) => (
          <li key={check.name} className="flex gap-2 text-xs">
            <span className={check.ok ? "text-emerald-400" : "text-rose-400"}>{check.ok ? "ok" : "no"}</span>
            <span className="text-slate-400">{check.name}</span>
            {!check.ok && check.detail !== "" && <span className="text-slate-500">{check.detail}</span>}
          </li>
        ))}
      </ul>
    </li>
  );
}

export default function Evaluation({ refreshKey }: Props) {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [runs, setRuns] = useState<EvalRunSummary[]>([]);
  const [detail, setDetail] = useState<EvalRunDetail | null>(null);
  const [traceId, setTraceId] = useState<string | null>(null);
  const [trace, setTrace] = useState<ExecutionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kindFilter, setKindFilter] = useState("all");

  useEffect(() => {
    let alive = true;
    api
      .evals(100)
      .then((rows) => {
        if (!alive) return;
        setRuns(rows);
        setError(null);
        setSelectedRunId((current) =>
          current !== null && rows.some((row) => row.run_id === current)
            ? current
            : (rows[0]?.run_id ?? null),
        );
      })
      .catch((cause: unknown) => {
        if (alive) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      alive = false;
    };
  }, [refreshKey]);

  useEffect(() => {
    if (selectedRunId === null) {
      setDetail(null);
      return;
    }
    let alive = true;
    api
      .eval(selectedRunId)
      .then((record) => {
        if (alive) setDetail(record);
      })
      .catch((cause: unknown) => {
        if (alive) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      alive = false;
    };
  }, [selectedRunId, refreshKey]);

  useEffect(() => {
    if (traceId === null) {
      setTrace(null);
      return;
    }
    let alive = true;
    api
      .adminExecution(traceId)
      .then((record) => {
        if (alive) setTrace(record);
      })
      .catch((cause: unknown) => {
        if (alive) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      alive = false;
    };
  }, [traceId]);

  const filtered = useMemo(
    () => runs.filter((run) => kindFilter === "all" || run.kind === kindFilter),
    [runs, kindFilter],
  );

  return (
    <div className="grid h-[calc(100vh-7rem)] min-h-[32rem] gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
      <aside className="flex min-h-0 flex-col rounded-lg border border-edge bg-panel/60 p-3">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-slate-200">Evaluation runs</h2>
          <select
            value={kindFilter}
            onChange={(event) => setKindFilter(event.target.value)}
            className="rounded border border-edge bg-panel/60 px-2 py-1 text-xs text-slate-300 outline-none focus:border-cyan-500/70"
          >
            <option value="all">All</option>
            <option value="agent">agent</option>
            <option value="chat">chat</option>
          </select>
        </div>
        {error !== null && <p className="mb-2 text-xs text-rose-300">{error}</p>}
        <div className="min-h-0 flex-1 space-y-1.5 overflow-auto pr-1">
          {filtered.length === 0 ? (
            <p className="px-1 text-sm text-slate-500">
              No evaluation runs recorded yet. Run{" "}
              <code className="text-slate-400">python -m lolcoach.cli eval-agents</code>.
            </p>
          ) : (
            filtered.map((run) => (
              <RunCard
                key={run.run_id}
                run={run}
                active={run.run_id === selectedRunId}
                onSelect={() => setSelectedRunId(run.run_id)}
              />
            ))
          )}
        </div>
      </aside>

      <section className="flex min-h-0 flex-col rounded-lg border border-edge bg-panel/60 p-4">
        {detail === null ? (
          <div className="flex h-full items-center justify-center">
            <p className="text-sm text-slate-500">Select an evaluation run to see every scenario and its checks.</p>
          </div>
        ) : (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-3 border-b border-edge pb-3">
              <h2 className="text-sm font-semibold text-slate-100">
                {detail.kind} evaluation
              </h2>
              <StatusBadge status={detail.ok ? "succeeded" : "failed"} />
              <span className={`font-mono text-sm ${detail.ok ? "text-emerald-300" : "text-rose-300"}`}>
                {detail.passed}/{detail.total} passed
              </span>
              <span className="font-mono text-xs text-slate-500">
                {detail.provider}
                {detail.model === "" ? "" : `/${detail.model}`}
              </span>
              <span className="text-xs text-slate-500">
                {detail.started_at.replace("T", " ").slice(0, 19)} →{" "}
                {detail.finished_at === null ? "running" : detail.finished_at.replace("T", " ").slice(11, 19)}
              </span>
              <span className="ml-auto font-mono text-[11px] text-slate-600">{detail.run_id.slice(0, 8)}</span>
            </div>
            <ul
              className={`min-h-0 divide-y divide-edge/60 overflow-auto ${
                trace === null ? "flex-1" : "max-h-[40%] shrink-0"
              }`}
            >
              {detail.scenarios.map((scenario) => (
                <CheckRow
                  key={`${scenario.id}-${scenario.execution_id}`}
                  scenario={scenario}
                  active={scenario.execution_id === traceId}
                  onOpenTrace={() => setTraceId(scenario.execution_id)}
                />
              ))}
            </ul>
            {trace !== null && (
              <div className="mt-3 flex min-h-0 flex-1 flex-col rounded-lg border border-edge bg-[#0c1322] p-2">
                <div className="mb-2 flex items-center gap-2 px-1">
                  <span className="text-xs text-slate-500">scenario trace</span>
                  <span className="font-mono text-xs text-slate-400">{trace.execution_id}</span>
                  <span className="font-mono text-[11px] text-slate-600">{trace.workflow}</span>
                  <button
                    type="button"
                    onClick={() => setTraceId(null)}
                    className="ml-auto rounded border border-edge bg-panel/60 px-2 py-1 text-[11px] font-medium text-slate-300 transition hover:border-cyan-500/70 hover:text-cyan-200"
                  >
                    Close
                  </button>
                </div>
                <div className="min-h-0 flex-1">
                  <TraceGraph detail={trace} />
                </div>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
