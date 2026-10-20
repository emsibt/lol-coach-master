import type { DraftHistoryEntry } from "../types";

interface Props {
  entries: DraftHistoryEntry[];
  activeId: string | null;
  loading: boolean;
  error: string | null;
  nameOf: (slug: string) => string;
  onSelect: (entry: DraftHistoryEntry) => void;
  onNew: () => void;
  onOpenTrace: (executionId: string) => void;
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function teamLabel(slugs: string[], nameOf: (slug: string) => string): string {
  if (slugs.length === 0) return "—";
  return slugs.map(nameOf).join(", ");
}

export default function DraftHistory({
  entries,
  activeId,
  loading,
  error,
  nameOf,
  onSelect,
  onNew,
  onOpenTrace,
}: Props) {
  return (
    <aside className="flex h-full min-h-0 flex-col rounded-lg border border-edge bg-panel/60">
      <div className="border-b border-edge p-2">
        <button
          type="button"
          onClick={onNew}
          className="w-full rounded border border-cyan-500/40 bg-cyan-500/10 px-3 py-2 text-sm font-semibold text-cyan-300 transition hover:bg-cyan-500/20"
        >
          + New draft
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {error !== null && <p className="px-2 py-2 text-xs text-rose-300">{error}</p>}
        {loading && entries.length === 0 && (
          <p className="px-2 py-4 text-xs text-slate-500">Loading history…</p>
        )}
        {!loading && entries.length === 0 && error === null && (
          <p className="px-2 py-4 text-xs text-slate-500">
            No drafts yet. Evaluate a board to start the history.
          </p>
        )}
        <ul className="space-y-1">
          {entries.map((entry) => {
            const active = entry.execution_id === activeId;
            const failed = entry.status !== "succeeded";
            return (
              <li key={entry.execution_id}>
                <div
                  className={`group rounded border px-2 py-2 text-sm transition ${
                    active
                      ? "border-cyan-500/40 bg-cyan-500/10"
                      : "border-transparent hover:border-edge hover:bg-ink/40"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => onSelect(entry)}
                    className="block w-full text-left"
                    title="Restore this draft"
                  >
                    <span className="line-clamp-2 text-slate-200">
                      {teamLabel(entry.draft.allies, nameOf)}
                      <span className="mx-1 text-slate-500">vs</span>
                      {teamLabel(entry.draft.enemies, nameOf)}
                    </span>
                    <span className="mt-0.5 flex items-center gap-2 text-[11px] text-slate-500">
                      <span>{relativeTime(entry.started_at)}</span>
                      <span className={failed ? "text-rose-300" : "text-emerald-300"}>
                        {failed ? "failed" : "ok"}
                      </span>
                      {entry.counter !== null && (
                        <span className="truncate text-slate-400">↳ {entry.counter.name}</span>
                      )}
                    </span>
                  </button>
                  <div className="mt-1 flex gap-2 text-[11px] opacity-0 transition group-hover:opacity-100">
                    <button
                      type="button"
                      onClick={() => onOpenTrace(entry.execution_id)}
                      className="text-slate-500 hover:text-slate-300"
                    >
                      trace
                    </button>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </aside>
  );
}
