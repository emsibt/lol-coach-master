import type {
  AgentBoard,
  AgentChatResponse,
  AgentImage,
  AgentTurn,
  Champion,
  ChatResponse,
  DraftHistoryEntry,
  DraftRequest,
  EvalRunDetail,
  EvalRunSummary,
  EvaluateResponse,
  ExecutionDetail,
  ExecutionSummary,
  Health,
  RerunResponse,
  SessionDetail,
  SessionSummary,
} from "./types";

// Empty by default: the Vite dev server proxies /api to the backend. Set
// VITE_API_BASE_URL to target a different host (e.g. a deployed API).
const BASE: string = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${detail}`);
  }
  return (await response.json()) as T;
}

async function requestVoid(path: string, init?: RequestInit): Promise<void> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${detail}`);
  }
}

export const api = {
  health: (): Promise<Health> => request<Health>("/api/health"),
  champions: (): Promise<Champion[]> => request<Champion[]>("/api/champions"),
  evaluate: (draft: DraftRequest): Promise<EvaluateResponse> =>
    request<EvaluateResponse>("/api/draft/evaluate", {
      method: "POST",
      body: JSON.stringify(draft),
    }),
  drafts: (limit = 50): Promise<DraftHistoryEntry[]> =>
    request<DraftHistoryEntry[]>(`/api/drafts?limit=${limit}`),
  executions: (limit = 50, workflow?: string): Promise<ExecutionSummary[]> =>
    request<ExecutionSummary[]>(
      `/api/executions?limit=${limit}${workflow ? `&workflow=${encodeURIComponent(workflow)}` : ""}`,
    ),
  execution: (id: string): Promise<ExecutionDetail> =>
    request<ExecutionDetail>(`/api/executions/${encodeURIComponent(id)}`),
  rerun: (id: string): Promise<RerunResponse> =>
    request<RerunResponse>(`/api/executions/${encodeURIComponent(id)}/rerun`, { method: "POST" }),
  // The maintenance-agent surface: served from /api/admin, rendered by the admin
  // views at /admin, deliberately apart from the coach application.
  adminExecutions: (limit = 50, workflow?: string): Promise<ExecutionSummary[]> =>
    request<ExecutionSummary[]>(
      `/api/admin/executions?limit=${limit}${workflow ? `&workflow=${encodeURIComponent(workflow)}` : ""}`,
    ),
  adminExecution: (id: string): Promise<ExecutionDetail> =>
    request<ExecutionDetail>(`/api/admin/executions/${encodeURIComponent(id)}`),
  evals: (limit = 50, kind?: string): Promise<EvalRunSummary[]> =>
    request<EvalRunSummary[]>(
      `/api/admin/evals?limit=${limit}${kind ? `&kind=${encodeURIComponent(kind)}` : ""}`,
    ),
  eval: (runId: string): Promise<EvalRunDetail> =>
    request<EvalRunDetail>(`/api/admin/evals/${encodeURIComponent(runId)}`),
  chat: (executionId: string, message: string): Promise<ChatResponse> =>
    request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ execution_id: executionId, message }),
    }),
  agentChat: (payload: {
    message: string;
    board?: Partial<AgentBoard>;
    image?: AgentImage | null;
    turns?: AgentTurn[];
    session_id?: string;
  }): Promise<AgentChatResponse> =>
    request<AgentChatResponse>("/api/agent/chat", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  sessions: (limit = 100): Promise<SessionSummary[]> =>
    request<SessionSummary[]>(`/api/sessions?limit=${limit}`),
  session: (id: string): Promise<SessionDetail> =>
    request<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}`),
  createSession: (board?: Partial<AgentBoard>): Promise<SessionDetail> =>
    request<SessionDetail>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ board }),
    }),
  renameSession: (id: string, title: string): Promise<SessionDetail> =>
    request<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  deleteSession: (id: string): Promise<void> =>
    requestVoid(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),
};
