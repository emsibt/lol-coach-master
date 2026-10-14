import { useEffect, useState } from "react";

import { api } from "../api";
import type { EventRecord, ExecutionDetail, ExecutionSummary } from "../types";
import { EventBadge, StatusBadge } from "./Badges";

interface Props {
  selectedId: string | null;
  onSelect: (id: string) => void;
  refreshKey: number;
}

function Json({ label, value }: { label: string; value: unknown }) {
  if (value === null || value === undefined) return null;
  return (
    <div>
      <p className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">{label}</p>
      <pre className="max-h-64 overflow-auto rounded border border-edge bg-ink/60 p-2 font-mono text-[11px] leading-relaxed text-slate-300">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function EventRow({ event }: { event: EventRecord }) {
  const failed = event.type.includes("FAILED");
  return (
    <li
      className={`rounded border ${
        failed ? "border-rose-500/40 bg-rose-500/5" : "border-edge bg-panel/40"
      }`}
    >
      <details>
        <summary className="flex cursor-pointer list-none items-center gap-3 px-3 py-2">
          <span className="w-6 text-right font-mono text-xs text-slate-500">{event.seq}</span>
          <EventBadge type={event.type} />
          <span className="text-sm text-slate-300">{event.state_name ?? "—"}</span>
          <span className="ml-auto font-mono text-xs text-slate-500">
            {event.duration_ms !== null ? `${event.duration_ms} ms` : ""}
          </span>
        </summary>
        <div className="space-y-2 border-t border-edge/60 px-3 py-2">
          {event.error !== null && <p className="text-sm text-rose-300">{event.error}</p>}
          <Json label="input" value={event.input} />
          <Json label="output" value={event.output} />
          {event.traceback !== null && (
            <div>
              <p className="mb-1 text-[10px] uppercase tracking-wide text-rose-400">traceback</p>
              <pre className="max-h-64 overflow-auto rounded border border-rose-500/40 bg-rose-950/30 p-2 font-mono text-[11px] leading-relaxed text-rose-200">
                {event.traceback}
              </pre>
            </div>
          )}
        </div>
      </details>
    </li>
  );
}

export default function TraceInspector({ selectedId, onSelect, refreshKey }: Props) {
  const [summaries, setSummaries] = useState<ExecutionSummary[]>([]);
  const [detail, setDetail] = useState<ExecutionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

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
  }, [refreshKey]);

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

  return (
    <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
      <section className="rounded-lg border border-edge bg-panel/60 p-3">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-200">Executions</h2>
          {loading && <span className="text-xs text-slate-500">loading…</span>}
        </div>
        {error !== null && <p className="mb-2 text-xs text-rose-300">{error}</p>}
        {summaries.length === 0 && !loading && (
          <p className="text-sm text-slate-500">No executions yet. Evaluate a draft.</p>
        )}
        <ul className="space-y-1.5">
          {summaries.map((row) => (
            <li key={row.execution_id}>
              <button
                type="button"
                onClick={() => onSelect(row.execution_id)}
                className={`w-full rounded border px-2 py-2 text-left transition ${
                  row.execution_id === selectedId
                    ? "border-cyan-500/70 bg-cyan-500/10"
                    : "border-edge bg-ink/40 hover:border-slate-500"
                }`}
              >
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-xs text-slate-300">
                    {row.execution_id.slice(0, 12)}
                  </span>
                  <StatusBadge status={row.status} />
                </div>
                <div className="text-[11px] text-slate-500">
                  {row.workflow} · {row.started_at.replace("T", " ").slice(0, 19)}
                </div>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="rounded-lg border border-edge bg-panel/60 p-4">
        {detail === null ? (
          <p className="text-sm text-slate-500">Select an execution to inspect its timeline.</p>
        ) : (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <h2 className="text-sm font-semibold text-slate-200">Timeline</h2>
              <StatusBadge status={detail.status} />
              <span className="font-mono text-xs text-slate-500">{detail.execution_id}</span>
              <span className="text-xs text-slate-500">{detail.events.length} events</span>
              {detail.error !== null && <span className="text-xs text-rose-300">{detail.error}</span>}
            </div>
            <ol className="space-y-1.5">
              {detail.events.map((event) => (
                <EventRow key={event.seq} event={event} />
              ))}
            </ol>
          </>
        )}
      </section>
    </div>
  );
}
