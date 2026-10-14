// Response shapes mirrored from the backend (see lolcoach/server/schemas.py).

export const LANES = ["top", "jungle", "mid", "adc", "support"] as const;
export type Lane = (typeof LANES)[number];

export interface Health {
  status: string;
  service: string;
  version: string;
}

export interface PositionInfo {
  position: string;
  win_rate: number;
  pick_rate: number;
  ban_rate: number;
  tier: number | null;
  roles: string[];
}

export interface Champion {
  slug: string;
  name: string;
  positions: PositionInfo[];
}

export interface TraceLine {
  kind: string;
  source: string;
  text: string;
  delta: number;
}

export interface Pick {
  slug: string;
  name: string;
  role: string;
  score: number;
  traces: TraceLine[];
}

export interface DraftRequest {
  allies: string[];
  enemies: string[];
  ally_bans: string[];
  enemy_bans: string[];
  open_roles: string[];
}

export interface EvaluateResponse {
  execution_id: string;
  status: string;
  error: string | null;
  problems: string[];
  counter: Pick | null;
  synergy: Pick | null;
  traces: TraceLine[];
}

export interface ExecutionSummary {
  execution_id: string;
  workflow: string;
  version: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  error: string | null;
}

export interface EventRecord {
  seq: number;
  type: string;
  state_name: string | null;
  timestamp: string;
  input: unknown;
  output: unknown;
  error: string | null;
  traceback: string | null;
  duration_ms: number | null;
}

export interface ExecutionDetail extends ExecutionSummary {
  input: unknown;
  output: unknown;
  events: EventRecord[];
}

export interface ChatResponse {
  reply: string;
  mode: string;
}

export interface AgentImage {
  media_type: string;
  data_b64: string;
}

export interface AgentBoard {
  allies: string[];
  enemies: string[];
  ally_bans: string[];
  enemy_bans: string[];
  open_roles: string[];
}

export interface AgentTurn {
  role: string;
  text: string;
}

export interface AgentToolCall {
  name: string;
  ok: boolean;
  arguments: Record<string, unknown>;
}

// Mirrors lolcoach/server/schemas.py::AgentChatResponse.
export interface AgentChatResponse {
  execution_id: string;
  status: string;
  reply: string;
  mode: string;
  board: AgentBoard;
  unresolved: string[];
  tool_calls: AgentToolCall[];
  counter: Pick | null;
  synergy: Pick | null;
  traces: TraceLine[];
  error: string | null;
}
