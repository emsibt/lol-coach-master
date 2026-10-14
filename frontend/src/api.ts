import type {
  AgentBoard,
  AgentChatResponse,
  AgentImage,
  AgentTurn,
  Champion,
  ChatResponse,
  DraftRequest,
  EvaluateResponse,
  ExecutionDetail,
  ExecutionSummary,
  Health,
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

export const api = {
  health: (): Promise<Health> => request<Health>("/api/health"),
  champions: (): Promise<Champion[]> => request<Champion[]>("/api/champions"),
  evaluate: (draft: DraftRequest): Promise<EvaluateResponse> =>
    request<EvaluateResponse>("/api/draft/evaluate", {
      method: "POST",
      body: JSON.stringify(draft),
    }),
  executions: (limit = 50): Promise<ExecutionSummary[]> =>
    request<ExecutionSummary[]>(`/api/executions?limit=${limit}`),
  execution: (id: string): Promise<ExecutionDetail> =>
    request<ExecutionDetail>(`/api/executions/${encodeURIComponent(id)}`),
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
  }): Promise<AgentChatResponse> =>
    request<AgentChatResponse>("/api/agent/chat", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
