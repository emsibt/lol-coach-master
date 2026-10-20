import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import {
  LANES,
  type Champion,
  type DraftHistoryEntry,
  type DraftRequest,
  type EvaluateResponse,
} from "../types";
import ChampionAvatar from "./ChampionAvatar";
import ChampionGrid from "./ChampionGrid";
import DraftHistory from "./DraftHistory";
import RecommendationCard from "./RecommendationCard";

type Target =
  | { kind: "allyPick"; role: string }
  | { kind: "enemyPick"; role: string }
  | { kind: "allyBan" }
  | { kind: "enemyBan" };

interface Props {
  champions: Champion[];
  onEvaluated: (response: EvaluateResponse) => void;
  onOpenTrace: (executionId: string) => void;
}

/** Lay the recorded role map over the slug list, filling any gaps in lane order. */
function mergeRoles(
  slugs: string[],
  explicit: Record<string, string> | undefined,
  fallbackLanes: readonly string[],
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const role of LANES) {
    const slug = explicit?.[role];
    if (slug !== undefined && slugs.includes(slug) && !Object.values(out).includes(slug)) {
      out[role] = slug;
    }
  }
  const placed = new Set(Object.values(out));
  const free = fallbackLanes.filter((role) => out[role] === undefined);
  let index = 0;
  for (const slug of slugs) {
    if (placed.has(slug)) continue;
    if (index >= free.length) break;
    out[free[index]] = slug;
    placed.add(slug);
    index += 1;
  }
  return out;
}

function targetLabel(target: Target): string {
  switch (target.kind) {
    case "allyPick":
      return `Allies · ${target.role}`;
    case "enemyPick":
      return `Enemies · ${target.role}`;
    case "allyBan":
      return "Allies · ban";
    case "enemyBan":
      return "Enemies · ban";
  }
}

export default function DraftBoard({ champions, onEvaluated, onOpenTrace }: Props) {
  const [allyPicks, setAllyPicks] = useState<Record<string, string>>({});
  const [enemyPicks, setEnemyPicks] = useState<Record<string, string>>({});
  const [allyBans, setAllyBans] = useState<string[]>([]);
  const [enemyBans, setEnemyBans] = useState<string[]>([]);
  const [target, setTarget] = useState<Target | null>(null);
  const [result, setResult] = useState<EvaluateResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [history, setHistory] = useState<DraftHistoryEntry[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [activeHistoryId, setActiveHistoryId] = useState<string | null>(null);

  const refreshHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      setHistory(await api.drafts(100));
      setHistoryError(null);
    } catch (cause) {
      setHistoryError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshHistory();
  }, [refreshHistory]);

  const nameBySlug = useMemo(() => {
    const map = new Map<string, string>();
    for (const champion of champions) map.set(champion.slug, champion.name);
    return map;
  }, [champions]);

  const keyBySlug = useMemo(() => {
    const map = new Map<string, string>();
    for (const champion of champions) map.set(champion.slug, champion.key);
    return map;
  }, [champions]);

  const nameOf = (slug: string): string => nameBySlug.get(slug) ?? slug;
  const keyOf = (slug: string): string => keyBySlug.get(slug) ?? "";

  function withoutSlug(record: Record<string, string>, slug: string): Record<string, string> {
    const next: Record<string, string> = {};
    for (const [role, value] of Object.entries(record)) if (value !== slug) next[role] = value;
    return next;
  }

  function assign(slug: string) {
    if (target === null) return;
    setAllyPicks((prev) => withoutSlug(prev, slug));
    setEnemyPicks((prev) => withoutSlug(prev, slug));
    setAllyBans((prev) => prev.filter((s) => s !== slug));
    setEnemyBans((prev) => prev.filter((s) => s !== slug));

    if (target.kind === "allyPick") {
      setAllyPicks((prev) => ({ ...withoutSlug(prev, slug), [target.role]: slug }));
    } else if (target.kind === "enemyPick") {
      setEnemyPicks((prev) => ({ ...withoutSlug(prev, slug), [target.role]: slug }));
    } else if (target.kind === "allyBan") {
      setAllyBans((prev) => (prev.includes(slug) || prev.length >= 5 ? prev : [...prev, slug]));
    } else {
      setEnemyBans((prev) => (prev.includes(slug) || prev.length >= 5 ? prev : [...prev, slug]));
    }
  }

  function clearBoard() {
    setAllyPicks({});
    setEnemyPicks({});
    setAllyBans([]);
    setEnemyBans([]);
    setTarget(null);
    setResult(null);
    setError(null);
    setActiveHistoryId(null);
  }

  function restoreDraft(entry: DraftHistoryEntry) {
    const draft = entry.draft;
    const openRoles = draft.open_roles ?? [];
    const filled = LANES.filter((role) => !openRoles.includes(role));
    setAllyPicks(
      mergeRoles(
        draft.allies,
        draft.ally_roles,
        filled.length >= draft.allies.length ? filled : LANES,
      ),
    );
    setEnemyPicks(mergeRoles(draft.enemies, draft.enemy_roles, LANES));
    setAllyBans(draft.ally_bans);
    setEnemyBans(draft.enemy_bans);
    setTarget(null);
    setError(null);
    setResult({
      execution_id: entry.execution_id,
      status: entry.status,
      error: entry.error,
      problems: entry.problems,
      counter: entry.counter,
      synergy: entry.synergy,
      traces: [],
    });
    setActiveHistoryId(entry.execution_id);
  }

  async function evaluate() {
    setBusy(true);
    setError(null);
    const openRoles = LANES.filter((role) => allyPicks[role] === undefined);
    const draft: DraftRequest = {
      allies: Object.values(allyPicks),
      enemies: Object.values(enemyPicks),
      ally_bans: allyBans,
      enemy_bans: enemyBans,
      open_roles: openRoles.length > 0 ? [...openRoles] : [...LANES],
      ally_roles: allyPicks,
      enemy_roles: enemyPicks,
    };
    try {
      const response = await api.evaluate(draft);
      setResult(response);
      setTarget(null);
      setActiveHistoryId(response.execution_id);
      onEvaluated(response);
      await refreshHistory();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  const teamPanel = (
    side: "ally" | "enemy",
    title: string,
    picks: Record<string, string>,
    bans: string[],
  ) => (
    <div className="rounded-lg border border-edge bg-panel/60 p-3">
      <h2 className="mb-2 text-sm font-semibold text-slate-200">{title}</h2>
      <div className="space-y-1">
        {LANES.map((role) => {
          const selected =
            target?.kind === (side === "ally" ? "allyPick" : "enemyPick") && target.role === role;
          const slug = picks[role];
          return (
            <button
              key={role}
              type="button"
              onClick={() => setTarget({ kind: side === "ally" ? "allyPick" : "enemyPick", role })}
              className={`flex w-full items-center justify-between rounded border px-2 py-1.5 text-left text-sm transition ${
                selected
                  ? "border-cyan-500/70 bg-cyan-500/10"
                  : "border-edge bg-ink/40 hover:border-slate-500"
              }`}
            >
              <span className="w-16 text-xs uppercase tracking-wide text-slate-500">{role}</span>
              {slug === undefined ? (
                <span className="text-slate-600">—</span>
              ) : (
                <span className="flex min-w-0 items-center gap-2">
                  <ChampionAvatar name={nameOf(slug)} championKey={keyOf(slug)} size="sm" />
                  <span className="truncate font-medium text-slate-100">{nameOf(slug)}</span>
                </span>
              )}
            </button>
          );
        })}
      </div>

      <div className="mt-3">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-xs uppercase tracking-wide text-slate-500">Bans</span>
          <button
            type="button"
            onClick={() => setTarget({ kind: side === "ally" ? "allyBan" : "enemyBan" })}
            className={`rounded border px-2 py-0.5 text-xs transition ${
              target?.kind === (side === "ally" ? "allyBan" : "enemyBan")
                ? "border-cyan-500/70 bg-cyan-500/10 text-cyan-300"
                : "border-edge text-slate-400 hover:border-slate-500"
            }`}
          >
            + ban
          </button>
        </div>
        <div className="flex min-h-[26px] flex-wrap gap-1">
          {bans.map((slug) => (
            <span
              key={slug}
              className="flex items-center gap-1 rounded border border-rose-500/40 bg-rose-500/10 px-1.5 py-0.5 text-xs text-rose-200"
            >
              <ChampionAvatar name={nameOf(slug)} championKey={keyOf(slug)} size="sm" />
              {nameOf(slug)}
              <button
                type="button"
                onClick={() =>
                  side === "ally"
                    ? setAllyBans((prev) => prev.filter((s) => s !== slug))
                    : setEnemyBans((prev) => prev.filter((s) => s !== slug))
                }
                className="text-rose-300 hover:text-rose-100"
                aria-label={`remove ${nameOf(slug)} ban`}
              >
                ×
              </button>
            </span>
          ))}
          {bans.length === 0 && <span className="text-xs text-slate-600">no bans</span>}
        </div>
      </div>
    </div>
  );

  return (
    <div className="grid h-[calc(100vh-7rem)] min-h-[28rem] grid-cols-1 gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
      <div className="min-h-0">
        <DraftHistory
          entries={history}
          activeId={activeHistoryId}
          loading={historyLoading}
          error={historyError}
          nameOf={nameOf}
          onSelect={restoreDraft}
          onNew={clearBoard}
          onOpenTrace={onOpenTrace}
        />
      </div>

      <div className="flex min-h-0 flex-col gap-4 overflow-y-auto">
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_minmax(0,1fr)]">
          {teamPanel("ally", "Our team", allyPicks, allyBans)}
          <ChampionGrid
            champions={champions}
            targetLabel={target === null ? null : targetLabel(target)}
            onPick={assign}
          />
          {teamPanel("enemy", "Enemy team", enemyPicks, enemyBans)}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => void evaluate()}
            disabled={busy}
            className="rounded bg-cyan-500 px-4 py-2 text-sm font-semibold text-ink transition hover:bg-cyan-400 disabled:opacity-50"
          >
            {busy ? "Evaluating…" : "Evaluate draft"}
          </button>
          <button
            type="button"
            onClick={clearBoard}
            className="rounded border border-edge px-3 py-2 text-sm text-slate-300 transition hover:border-slate-500"
          >
            Clear board
          </button>
          {error !== null && <span className="text-sm text-rose-300">{error}</span>}
          {result !== null && result.status === "failed" && (
            <span className="text-sm text-rose-300">
              Draft rejected: {result.error ?? "invalid board"}
              {result.problems.length > 0 && ` (${result.problems.join("; ")})`}
            </span>
          )}
        </div>

        {result !== null && (
          <div className="flex flex-col gap-3 md:flex-row">
            <RecommendationCard
              title="Counter-pick"
              pick={result.counter}
              championKey={result.counter ? keyOf(result.counter.slug) : undefined}
              empty="No counter-pick: add an enemy champion to score against."
            />
            <RecommendationCard
              title="Synergy-pick"
              pick={result.synergy}
              championKey={result.synergy ? keyOf(result.synergy.slug) : undefined}
              empty="No synergy-pick: add an ally champion to pair with."
            />
          </div>
        )}
      </div>
    </div>
  );
}
