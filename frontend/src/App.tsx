import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import { DdragonVersionContext } from "./ddragon";
import type {
  AgentBoard,
  AgentChatResponse,
  AgentImage,
  Champion,
  CoachMessage,
  EvaluateResponse,
  Health,
  SessionDetail,
  SessionSummary,
} from "./types";
import Admin from "./Admin";
import BoardSummary from "./components/BoardSummary";
import ChatPanel from "./components/ChatPanel";
import DraftBoard from "./components/DraftBoard";
import Landing from "./components/Landing";
import SessionSidebar from "./components/SessionSidebar";
import TabButton from "./components/TabButton";
import TraceInspector from "./components/TraceInspector";

type Tab = "coach" | "draft" | "traces";

const EMPTY_BOARD: AgentBoard = {
  allies: [],
  enemies: [],
  ally_bans: [],
  enemy_bans: [],
  open_roles: [],
};

let nextMessageId = 1;

function messagesFromSession(session: SessionDetail): CoachMessage[] {
  return session.messages.map((message) => ({
    id: nextMessageId++,
    role: message.role === "user" ? "user" : "coach",
    text: message.text,
    mode: message.mode ?? undefined,
    toolCalls: message.tool_calls,
    counter: message.role === "assistant" ? message.counter : undefined,
    synergy: message.role === "assistant" ? message.synergy : undefined,
    executionId: message.execution_id ?? undefined,
    error: message.error !== null,
  }));
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

  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<CoachMessage[]>([]);
  const [remaining, setRemaining] = useState<number | null>(null);
  const [maxQuestions, setMaxQuestions] = useState(0);
  const [busy, setBusy] = useState(false);

  const refreshSessions = useCallback(async (): Promise<SessionSummary[]> => {
    const list = await api.sessions();
    setSessions(list);
    return list;
  }, []);

  const openSession = useCallback(async (sessionId: string) => {
    const detail = await api.session(sessionId);
    setActiveId(detail.session_id);
    setMessages(messagesFromSession(detail));
    setBoard(detail.board);
    setUnresolved([]);
    setRemaining(detail.remaining_questions);
    setMaxQuestions(detail.max_questions);
    setExecutionId(null);
  }, []);

  useEffect(() => {
    api
      .champions()
      .then(setChampions)
      .catch((cause: unknown) => setLoadError(cause instanceof Error ? cause.message : String(cause)));
    api.health().then(setHealth).catch(() => setHealth(null));
    void refreshSessions().then((list) => {
      if (list.length > 0) void openSession(list[0].session_id);
    });
  }, [refreshSessions, openSession]);

  const onEvaluated = useCallback((response: EvaluateResponse) => {
    setExecutionId(response.execution_id);
    setRefreshKey((key) => key + 1);
  }, []);

  const onNewChat = useCallback(async () => {
    const detail = await api.createSession();
    await refreshSessions();
    setActiveId(detail.session_id);
    setMessages([]);
    setBoard(EMPTY_BOARD);
    setUnresolved([]);
    setRemaining(detail.remaining_questions);
    setMaxQuestions(detail.max_questions);
    setExecutionId(null);
  }, [refreshSessions]);

  const onDeleteSession = useCallback(
    async (sessionId: string) => {
      await api.deleteSession(sessionId);
      const list = await refreshSessions();
      if (sessionId === activeId) {
        if (list.length > 0) await openSession(list[0].session_id);
        else {
          setActiveId(null);
          setMessages([]);
          setBoard(EMPTY_BOARD);
          setUnresolved([]);
          setRemaining(null);
          setMaxQuestions(0);
          setExecutionId(null);
        }
      }
    },
    [activeId, openSession, refreshSessions],
  );

  const onRenameSession = useCallback(
    async (sessionId: string, title: string) => {
      await api.renameSession(sessionId, title);
      await refreshSessions();
    },
    [refreshSessions],
  );

  const onSend = useCallback(
    async (message: string, image: AgentImage | null, imagePreview: string | null) => {
      let sessionId = activeId;
      let requestBoard: AgentBoard = board;
      try {
        if (sessionId === null) {
          const created = await api.createSession();
          sessionId = created.session_id;
          requestBoard = EMPTY_BOARD;
          setActiveId(sessionId);
          setBoard(EMPTY_BOARD);
          setUnresolved([]);
          setRemaining(created.remaining_questions);
          setMaxQuestions(created.max_questions);
        }
      } catch (cause) {
        const failure = cause instanceof Error ? cause.message : String(cause);
        setMessages((prev) => [...prev, { id: nextMessageId++, role: "coach", text: failure, error: true }]);
        return;
      }

      const displayText = message === "" ? "(draft image)" : message;
      setMessages((prev) => [
        ...prev,
        {
          id: nextMessageId++,
          role: "user",
          text: displayText,
          imagePreview: imagePreview ?? undefined,
        },
      ]);
      setBusy(true);
      try {
        const response: AgentChatResponse = await api.agentChat({
          message,
          board: requestBoard,
          image,
          session_id: sessionId,
        });
        const failed = response.status !== "succeeded";
        setMessages((prev) => [
          ...prev,
          {
            id: nextMessageId++,
            role: "coach",
            text: response.reply || response.error || "(no answer)",
            mode: response.mode,
            toolCalls: response.tool_calls,
            counter: response.counter,
            synergy: response.synergy,
            executionId: response.execution_id,
            error: failed,
          },
        ]);
        setBoard(response.board);
        setUnresolved(response.unresolved);
        setRemaining(response.remaining_questions);
        setMaxQuestions(response.max_questions);
        setExecutionId(response.execution_id);
        setRefreshKey((key) => key + 1);
      } catch (cause) {
        const failure = cause instanceof Error ? cause.message : String(cause);
        const limitReached = failure.includes("409");
        if (limitReached) setRemaining(0);
        setMessages((prev) => [...prev, { id: nextMessageId++, role: "coach", text: failure, error: true }]);
      } finally {
        setBusy(false);
        await refreshSessions();
      }
    },
    [activeId, board, refreshSessions],
  );

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

      <DdragonVersionContext.Provider value={health?.ddragon ?? ""}>
        <main className="mx-auto w-full max-w-[1400px] flex-1 p-4">
          {loadError !== null && (
            <p className="mb-3 rounded border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-200">
              Could not load champions: {loadError}
            </p>
          )}

          {tab === "coach" && (
            <div className="grid h-[calc(100vh-7rem)] min-h-[28rem] grid-cols-1 gap-4 lg:grid-cols-[240px_minmax(0,1fr)_300px]">
              <div className="min-h-0">
                <SessionSidebar
                  sessions={sessions}
                  activeId={activeId}
                  onSelect={(id) => void openSession(id)}
                  onNew={() => void onNewChat()}
                  onDelete={(id) => void onDeleteSession(id)}
                  onRename={(id, title) => void onRenameSession(id, title)}
                />
              </div>
              <div className="min-h-0">
                <ChatPanel
                  messages={messages}
                  busy={busy}
                  remaining={remaining}
                  maxQuestions={maxQuestions}
                  onSend={(message, image, preview) => void onSend(message, image, preview)}
                  onOpenTrace={() => setTab("traces")}
                />
              </div>
              <div className="min-h-0 overflow-y-auto">
                <BoardSummary board={board} champions={champions} unresolved={unresolved} />
              </div>
            </div>
          )}

          {tab === "draft" && (
            <DraftBoard
              champions={champions}
              onEvaluated={onEvaluated}
              onOpenTrace={(id) => {
                setExecutionId(id);
                setRefreshKey((key) => key + 1);
                setTab("traces");
              }}
            />
          )}

          {tab === "traces" && (
            <TraceInspector selectedId={executionId} onSelect={setExecutionId} refreshKey={refreshKey} />
          )}
        </main>
      </DdragonVersionContext.Provider>
    </div>
  );
}

export default function App() {
  const path = window.location.pathname;
  if (path.startsWith("/admin")) return <Admin />;
  if (path === "/") return <Landing />;
  return <Coach />;
}
