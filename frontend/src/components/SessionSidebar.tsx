import { useState } from "react";

import type { SessionSummary } from "../types";

interface Props {
  sessions: SessionSummary[];
  activeId: string | null;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
  onDelete: (sessionId: string) => void;
  onRename: (sessionId: string, title: string) => void;
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

export default function SessionSidebar({
  sessions,
  activeId,
  onSelect,
  onNew,
  onDelete,
  onRename,
}: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  function startRename(session: SessionSummary) {
    setEditingId(session.session_id);
    setDraft(session.title);
  }

  function commitRename(sessionId: string) {
    const title = draft.trim();
    if (title !== "") onRename(sessionId, title);
    setEditingId(null);
  }

  return (
    <aside className="flex h-full min-h-0 flex-col rounded-lg border border-edge bg-panel/60">
      <div className="border-b border-edge p-2">
        <button
          type="button"
          onClick={onNew}
          className="w-full rounded border border-cyan-500/40 bg-cyan-500/10 px-3 py-2 text-sm font-semibold text-cyan-300 transition hover:bg-cyan-500/20"
        >
          + New chat
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {sessions.length === 0 && (
          <p className="px-2 py-4 text-xs text-slate-500">No chats yet. Start one above.</p>
        )}
        <ul className="space-y-1">
          {sessions.map((session) => {
            const active = session.session_id === activeId;
            const full = session.remaining_questions <= 0;
            return (
              <li key={session.session_id}>
                <div
                  className={`group rounded border px-2 py-2 text-sm transition ${
                    active
                      ? "border-cyan-500/40 bg-cyan-500/10"
                      : "border-transparent hover:border-edge hover:bg-ink/40"
                  }`}
                >
                  {editingId === session.session_id ? (
                    <input
                      autoFocus
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      onBlur={() => commitRename(session.session_id)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") commitRename(session.session_id);
                        if (event.key === "Escape") setEditingId(null);
                      }}
                      className="w-full rounded border border-edge bg-ink/70 px-1.5 py-1 text-sm text-slate-200 outline-none focus:border-cyan-500/60"
                    />
                  ) : (
                    <button
                      type="button"
                      onClick={() => onSelect(session.session_id)}
                      onDoubleClick={() => startRename(session)}
                      className="block w-full text-left"
                      title={session.title}
                    >
                      <span className="line-clamp-1 text-slate-200">{session.title}</span>
                      <span className="mt-0.5 flex items-center gap-2 text-[11px] text-slate-500">
                        <span>{relativeTime(session.updated_at)}</span>
                        <span className={full ? "text-amber-400" : ""}>
                          {session.question_count}/{session.max_questions}
                        </span>
                      </span>
                    </button>
                  )}
                  <div className="mt-1 flex gap-2 text-[11px] opacity-0 transition group-hover:opacity-100">
                    <button
                      type="button"
                      onClick={() => startRename(session)}
                      className="text-slate-500 hover:text-slate-300"
                    >
                      rename
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (window.confirm(`Delete "${session.title}"?`)) {
                          onDelete(session.session_id);
                        }
                      }}
                      className="text-slate-500 hover:text-rose-300"
                    >
                      delete
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
