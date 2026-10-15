import type { AgentBoard, Champion } from "../types";
import ChampionAvatar from "./ChampionAvatar";

interface Props {
  board: AgentBoard;
  champions: Champion[];
  unresolved: string[];
}

function Side({
  title,
  slugs,
  tone,
  nameOf,
  keyOf,
}: {
  title: string;
  slugs: string[];
  tone: string;
  nameOf: (slug: string) => string;
  keyOf: (slug: string) => string;
}) {
  return (
    <div className="rounded border border-edge bg-ink/40 p-2">
      <p className={`mb-1 text-[10px] font-semibold uppercase tracking-wide ${tone}`}>{title}</p>
      {slugs.length === 0 ? (
        <p className="text-xs text-slate-600">empty</p>
      ) : (
        <ul className="flex flex-wrap gap-1">
          {slugs.map((slug) => (
            <li
              key={slug}
              className="flex items-center gap-1 rounded bg-panel/80 px-1.5 py-0.5 text-xs text-slate-200"
              title={slug}
            >
              <ChampionAvatar name={nameOf(slug)} championKey={keyOf(slug)} size="sm" />
              {nameOf(slug)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function BoardSummary({ board, champions, unresolved }: Props) {
  const byName = new Map(champions.map((champion) => [champion.slug, champion.name]));
  const nameOf = (slug: string) => byName.get(slug) ?? slug;
  const byKey = new Map(champions.map((champion) => [champion.slug, champion.key]));
  const keyOf = (slug: string) => byKey.get(slug) ?? "";

  return (
    <aside className="rounded-lg border border-edge bg-panel/60 p-3">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-200">Board</h2>
        <span className="text-xs text-slate-500">parsed by the agent</span>
      </div>
      <div className="space-y-2">
        <Side title="Our picks" slugs={board.allies} tone="text-cyan-400" nameOf={nameOf} keyOf={keyOf} />
        <Side title="Their picks" slugs={board.enemies} tone="text-rose-400" nameOf={nameOf} keyOf={keyOf} />
        <Side title="Our bans" slugs={board.ally_bans} tone="text-slate-400" nameOf={nameOf} keyOf={keyOf} />
        <Side title="Their bans" slugs={board.enemy_bans} tone="text-slate-400" nameOf={nameOf} keyOf={keyOf} />
      </div>
      {unresolved.length > 0 && (
        <p className="mt-3 rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-200">
          Not recognised: {unresolved.join(", ")}
        </p>
      )}
    </aside>
  );
}
