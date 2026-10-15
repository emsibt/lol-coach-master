import { useMemo, useState } from "react";

import type { Champion } from "../types";
import ChampionAvatar from "./ChampionAvatar";

interface Props {
  champions: Champion[];
  targetLabel: string | null;
  onPick: (slug: string) => void;
}

export default function ChampionGrid({ champions, targetLabel, onPick }: Props) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q === "") return champions;
    return champions.filter(
      (champion) =>
        champion.name.toLowerCase().includes(q) ||
        champion.slug.includes(q) ||
        champion.positions.some((p) => p.position.includes(q)),
    );
  }, [champions, query]);

  return (
    <section className="flex min-h-0 flex-col rounded-lg border border-edge bg-panel/60 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-200">Champion grid</h2>
        <span className="text-xs text-slate-500">{filtered.length} champions</span>
      </div>

      <input
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Search by name, role, or lane…"
        className="mb-2 w-full rounded border border-edge bg-ink/70 px-2 py-1.5 text-sm text-slate-200 outline-none placeholder:text-slate-600 focus:border-cyan-500/60"
      />

      <p className="mb-2 h-5 text-xs">
        {targetLabel === null ? (
          <span className="text-slate-500">Select a slot, then click a champion to place it.</span>
        ) : (
          <span className="text-cyan-300">Clicking a champion places it in: {targetLabel}</span>
        )}
      </p>

      <div className="min-h-0 flex-1 overflow-y-auto pr-1" style={{ maxHeight: "46vh" }}>
        <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 xl:grid-cols-4">
          {filtered.map((champion) => {
            const lanes = champion.positions.map((p) => p.position.slice(0, 2)).join(" ");
            return (
              <button
                key={champion.slug}
                type="button"
                disabled={targetLabel === null}
                onClick={() => onPick(champion.slug)}
                title={champion.positions.map((p) => p.position).join(", ")}
                className="flex items-center gap-2 rounded border border-edge bg-ink/50 px-2 py-1.5 text-left text-xs text-slate-300 transition enabled:hover:border-cyan-500/60 enabled:hover:bg-cyan-500/10 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <ChampionAvatar name={champion.name} championKey={champion.key} size="sm" />
                <span className="flex min-w-0 flex-col">
                  <span className="truncate font-medium text-slate-200">{champion.name}</span>
                  <span className="text-[10px] uppercase tracking-wide text-slate-500">{lanes}</span>
                </span>
              </button>
            );
          })}
        </div>
        {filtered.length === 0 && <p className="p-3 text-sm text-slate-500">No champions match.</p>}
      </div>
    </section>
  );
}
