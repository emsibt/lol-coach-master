import type { Pick } from "../types";
import { KindBadge } from "./Badges";
import ChampionAvatar from "./ChampionAvatar";

interface Props {
  title: string;
  pick: Pick | null;
  championKey?: string;
  empty: string;
}

function formatDelta(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(4)}`;
}

export default function RecommendationCard({ title, pick, championKey, empty }: Props) {
  return (
    <div className="flex-1 rounded-lg border border-edge bg-panel/60 p-4">
      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h3>
      {pick === null ? (
        <p className="text-sm text-slate-500">{empty}</p>
      ) : (
        <>
          <div className="mb-3 flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <ChampionAvatar name={pick.name} championKey={championKey ?? ""} size="lg" />
              <span className="text-xl font-semibold text-slate-100">{pick.name}</span>
              <span className="rounded bg-ink/70 px-1.5 py-0.5 text-xs uppercase text-slate-400">
                {pick.role}
              </span>
            </div>
            <span className="font-mono text-sm text-cyan-300">{formatDelta(pick.score)}</span>
          </div>
          <ul className="space-y-2">
            {pick.traces.map((trace, index) => (
              <li key={`${trace.source}-${index}`} className="rounded border border-edge bg-ink/40 p-2">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <KindBadge kind={trace.kind} />
                  <span className="font-mono text-xs text-slate-400">{formatDelta(trace.delta)}</span>
                </div>
                <p className="text-sm text-slate-300">{trace.text}</p>
                <p className="mt-1 break-all font-mono text-[10px] text-slate-600">{trace.source}</p>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
