import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import type { AgentBoard, AgentChatResponse, Champion, EvaluateResponse, Health } from "./types";
import BoardSummary from "./components/BoardSummary";
import ChatPanel from "./components/ChatPanel";
import DraftBoard from "./components/DraftBoard";
import Landing from "./components/Landing";
import TraceInspector from "./components/TraceInspector";

type Tab = "coach" | "draft" | "traces";

const EMPTY_BOARD: AgentBoard = {
  allies: [],
  enemies: [],
  ally_bans: [],
  enemy_bans: [],
  open_roles: [],
};

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded px-3 py-1.5 text-sm font-medium transition ${
        active ? "bg-cyan-500/15 text-cyan-300" : "text-slate-400 hover:text-slate-200"
      }`}
    >
      {children}
    </button>
  );
}

function Coach() {
  const [tab, setTab] = useState<Tab>("coach");
  const [champions, setChampions] = useState<Champion[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [board, setBoard] = useState<AgentBoard>(EMPTY_BOARD);
  const [unresolved, setUnresolved] = useState<string[]>([]);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    api
      .champions()
      .then(setChampions)
      .catch((cause: unknown) => setLoadError(cause instanceof Error ? cause.message : String(cause)));
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  const onEvaluated = useCallback((response: EvaluateResponse) => {
    setExecutionId(response.execution_id);
    setRefreshKey((key) => key + 1);
  }, []);

  const onAgentResponse = useCallback((response: AgentChatResponse) => {
    setBoard(response.board);
    setUnresolved(response.unresolved);
    setExecutionId(response.execution_id);
    setRefreshKey((key) => key + 1);
  }, []);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-edge bg-panel/50 px-4 py-3">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-3">
          <a href="/" className="text-lg font-bold text-slate-100">
            LoL Coach
          </a>
          <nav className="flex gap-1">
            <TabButton active={tab === "coach"} onClick={() => setTab("coach")}>
              Coach
            </TabButton>
            <TabButton active={tab === "draft"} onClick={() => setTab("draft")}>
              Draft
            </TabButton>
            <TabButton active={tab === "traces"} onClick={() => setTab("traces")}>
              Trace Inspector
            </TabButton>
          </nav>
          <div className="ml-auto font-mono text-xs text-slate-500">
            {health === null ? (
              <span className="text-rose-300">API offline</span>
            ) : (
              <span>API {health.version}</span>
            )}
            {executionId !== null && <span className="ml-3">exec {executionId.slice(0, 8)}</span>}
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1400px] flex-1 p-4">
        {loadError !== null && (
          <p className="mb-3 rounded border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-200">
            Could not load champions: {loadError}
          </p>
        )}

        {tab === "coach" && (
          <div className="grid h-[calc(100vh-7rem)] min-h-[28rem] grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
            <div className="min-h-0">
              <ChatPanel
                board={board}
                onResponse={onAgentResponse}
                onOpenTrace={() => setTab("traces")}
              />
            </div>
            <div className="min-h-0 overflow-y-auto">
              <BoardSummary board={board} champions={champions} unresolved={unresolved} />
            </div>
          </div>
        )}

        {tab === "draft" && <DraftBoard champions={champions} onEvaluated={onEvaluated} />}

        {tab === "traces" && (
          <TraceInspector selectedId={executionId} onSelect={setExecutionId} refreshKey={refreshKey} />
        )}
      </main>
    </div>
  );
}

export default function App() {
  if (window.location.pathname === "/") return <Landing />;
  return <Coach />;
}
