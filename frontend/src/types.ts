// Response shapes mirrored from the backend (see lolcoach/server/schemas.py).

export const LANES = ["top", "jungle", "mid", "adc", "support"] as const;
export type Lane = (typeof LANES)[number];

export interface Health {
  status: string;
  service: string;
  version: string;
  patch: string;
  ddragon: string;
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
  key: string;
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
  // Lane -> champion slug. Present on new evaluations; older history may omit it.
  ally_roles?: Record<string, string>;
  enemy_roles?: Record<string, string>;
}

export interface DraftHistoryEntry {
  execution_id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  error: string | null;
  draft: DraftRequest;
  problems: string[];
  counter: Pick | null;
  synergy: Pick | null;
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

export interface LogRecord {
  timestamp: string;
  level: string;
  logger: string;
  message: string;
  data?: Record<string, unknown> | null;
  exception?: string | null;
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
  logs: LogRecord[];
}

export interface ChoiceRule {
  predicate: string;
  next: string;
}

export interface WorkflowState {
  name: string;
  type: "task" | "choice" | "succeed" | "fail" | string;
  task: string | null;
  next: string | null;
  choices: ChoiceRule[];
  default: string | null;
  error: string | null;
}

export interface WorkflowGraph {
  name: string;
  version: string;
  start_at: string;
  states: WorkflowState[];
}

export interface ExecutionDetail extends ExecutionSummary {
  input: unknown;
  output: unknown;
  events: EventRecord[];
  graph: WorkflowGraph | null;
}

export interface RerunResponse {
  execution_id: string;
  status: string;
  error: string | null;
}

// Mirrors lolcoach/server/schemas.py::EvalCheckOut.
export interface EvalCheck {
  name: string;
  ok: boolean;
  detail: string;
}

// Mirrors lolcoach/server/schemas.py::EvalScenarioOut.
export interface EvalScenario {
  id: string;
  name: string;
  passed: boolean;
  execution_id: string;
  status: string;
  kind: string;
  mode: string;
  error: string | null;
  checks: EvalCheck[];
}

// Mirrors lolcoach/server/schemas.py::EvalRunSummary.
export interface EvalRunSummary {
  run_id: string;
  kind: string;
  provider: string;
  model: string;
  started_at: string;
  finished_at: string | null;
  total: number;
  passed: number;
  failed: number;
  ok: boolean;
}

// Mirrors lolcoach/server/schemas.py::EvalRunDetail.
export interface EvalRunDetail extends EvalRunSummary {
  scenarios: EvalScenario[];
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
  session_id: string | null;
  question_count: number;
  max_questions: number;
  remaining_questions: number;
}

// Mirrors lolcoach/server/schemas.py::SessionSummary.
export interface SessionSummary {
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  question_count: number;
  max_questions: number;
  remaining_questions: number;
}

// Mirrors lolcoach/server/schemas.py::SessionMessageOut.
export interface SessionMessage {
  seq: number;
  role: string;
  text: string;
  created_at: string;
  mode: string | null;
  tool_calls: AgentToolCall[];
  counter: Pick | null;
  synergy: Pick | null;
  execution_id: string | null;
  error: string | null;
}

export interface SessionDetail extends SessionSummary {
  board: AgentBoard;
  notes: string[];
  messages: SessionMessage[];
}

// The chat panel's view model. User messages are optimistic; the rest come from sessions.
export interface CoachMessage {
  id: number;
  role: "user" | "coach";
  text: string;
  mode?: string;
  imagePreview?: string;
  toolCalls?: AgentToolCall[];
  counter?: Pick | null;
  synergy?: Pick | null;
  executionId?: string;
  error?: boolean;
}
