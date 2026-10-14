import { useRef, useState } from "react";

import { api } from "../api";
import type { AgentBoard, AgentChatResponse, AgentImage, AgentToolCall, Pick } from "../types";
import RecommendationCard from "./RecommendationCard";

interface Props {
  board: AgentBoard;
  onResponse: (response: AgentChatResponse) => void;
  onOpenTrace: () => void;
}

interface CoachMessage {
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

let nextId = 1;

function readImage(file: File): Promise<{ image: AgentImage; preview: string }> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result);
      const data_b64 = result.slice(result.indexOf(",") + 1);
      resolve({
        image: { media_type: file.type === "" ? "image/png" : file.type, data_b64 },
        preview: result,
      });
    };
    reader.onerror = () => reject(new Error("could not read that image"));
    reader.readAsDataURL(file);
  });
}

function ToolChip({ call }: { call: AgentToolCall }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[10px] ${
        call.ok
          ? "border-cyan-500/40 bg-cyan-500/10 text-cyan-300"
          : "border-rose-500/40 bg-rose-500/10 text-rose-300"
      }`}
      title={JSON.stringify(call.arguments)}
    >
      {call.ok ? "✓" : "✗"} {call.name}
    </span>
  );
}

function CoachBubble({ message, onOpenTrace }: { message: CoachMessage; onOpenTrace: () => void }) {
  const picks = message.counter !== undefined || message.synergy !== undefined;
  return (
    <div
      className={`rounded-lg border px-3 py-2 text-sm ${
        message.error === true
          ? "border-rose-500/40 bg-rose-500/10 text-rose-200"
          : "border-cyan-500/30 bg-cyan-500/5 text-slate-200"
      }`}
    >
      {message.mode !== undefined && (
        <div className="mb-1 flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-wide text-cyan-400">
            {message.mode === "offline" ? "offline summary" : message.mode}
          </span>
          {message.toolCalls !== undefined && message.toolCalls.length > 0 && (
            <span className="flex flex-wrap gap-1">
              {message.toolCalls.map((call, index) => (
                <ToolChip key={`${call.name}-${index}`} call={call} />
              ))}
            </span>
          )}
        </div>
      )}
      <p className="whitespace-pre-wrap">{message.text}</p>
      {picks && (
        <div className="mt-3 flex flex-col gap-3 lg:flex-row">
          <RecommendationCard title="Counter-pick" pick={message.counter ?? null} empty="None available." />
          <RecommendationCard title="Synergy-pick" pick={message.synergy ?? null} empty="None available." />
        </div>
      )}
      {message.executionId !== undefined && (
        <button
          type="button"
          onClick={onOpenTrace}
          className="mt-2 font-mono text-[11px] text-slate-500 underline decoration-dotted hover:text-cyan-300"
        >
          trace {message.executionId.slice(0, 8)} · view journey
        </button>
      )}
    </div>
  );
}

export default function ChatPanel({ board, onResponse, onOpenTrace }: Props) {
  const [messages, setMessages] = useState<CoachMessage[]>([]);
  const [input, setInput] = useState("");
  const [attached, setAttached] = useState<{ image: AgentImage; preview: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [attachError, setAttachError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function attach(file: File) {
    try {
      setAttached(await readImage(file));
      setAttachError(null);
    } catch (cause) {
      setAttachError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function send() {
    const message = input.trim();
    if ((message === "" && attached === null) || busy) return;
    const turns = messages
      .filter((item) => item.text.trim() !== "")
      .map((item) => ({ role: item.role === "user" ? "user" : "assistant", text: item.text }));

    setMessages((prev) => [
      ...prev,
      { id: nextId++, role: "user", text: message === "" ? "(draft image)" : message, imagePreview: attached?.preview },
    ]);
    const image = attached?.image ?? null;
    setInput("");
    setAttached(null);
    setBusy(true);
    try {
      const response = await api.agentChat({ message, board, image, turns });
      setMessages((prev) => [
        ...prev,
        {
          id: nextId++,
          role: "coach",
          text: response.reply,
          mode: response.mode,
          toolCalls: response.tool_calls,
          counter: response.counter,
          synergy: response.synergy,
          executionId: response.execution_id,
        },
      ]);
      onResponse(response);
    } catch (cause) {
      const failure = cause instanceof Error ? cause.message : String(cause);
      setMessages((prev) => [...prev, { id: nextId++, role: "coach", text: failure, error: true }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex h-full min-h-0 flex-col rounded-lg border border-edge bg-panel/60">
      <div className="flex items-center justify-between border-b border-edge px-3 py-2">
        <h2 className="text-sm font-semibold text-slate-200">Coach</h2>
        <span className="text-xs text-slate-500">engine decides · agent translates</span>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {messages.length === 0 && (
          <div className="rounded-lg border border-edge bg-ink/40 p-4 text-sm text-slate-400">
            <p className="mb-2 text-slate-300">Ask in your own words — the agent turns it into engine calls.</p>
            <ul className="list-inside list-disc space-y-1 text-slate-500">
              <li>
                <span className="font-mono text-slate-400">jinx vs their zeri</span>
              </li>
              <li>
                <span className="font-mono text-slate-400">how does fizz do into ahri mid</span>
              </li>
              <li>paste a screenshot of the draft</li>
            </ul>
          </div>
        )}
        {messages.map((message) =>
          message.role === "user" ? (
            <div key={message.id} className="ml-auto max-w-[85%] rounded-lg border border-edge bg-ink/60 px-3 py-2">
              <p className="whitespace-pre-wrap text-sm text-slate-200">{message.text}</p>
              {message.imagePreview !== undefined && (
                <img
                  src={message.imagePreview}
                  alt="attached draft"
                  className="mt-2 max-h-40 rounded border border-edge"
                />
              )}
            </div>
          ) : (
            <CoachBubble key={message.id} message={message} onOpenTrace={onOpenTrace} />
          ),
        )}
        {busy && <p className="text-xs text-slate-500">thinking…</p>}
      </div>

      <form
        className="border-t border-edge p-3"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
        onPaste={(event) => {
          const file = Array.from(event.clipboardData.files)[0];
          if (file !== undefined) {
            event.preventDefault();
            void attach(file);
          }
        }}
      >
        {attached !== null && (
          <div className="mb-2 flex items-center gap-2 rounded border border-edge bg-ink/40 p-2">
            <img src={attached.preview} alt="draft to send" className="h-12 rounded" />
            <span className="text-xs text-slate-400">draft image attached</span>
            <button
              type="button"
              onClick={() => setAttached(null)}
              className="ml-auto text-xs text-slate-500 hover:text-rose-300"
            >
              remove
            </button>
          </div>
        )}
        {attachError !== null && <p className="mb-2 text-xs text-rose-300">{attachError}</p>}
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="rounded border border-edge px-2 py-1.5 text-sm text-slate-400 transition hover:text-slate-200"
            title="Attach a draft screenshot"
          >
            image
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file !== undefined) void attach(file);
              event.target.value = "";
            }}
          />
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            disabled={busy}
            placeholder="jinx vs their zeri"
            className="min-w-0 flex-1 rounded border border-edge bg-ink/70 px-2 py-1.5 text-sm text-slate-200 outline-none placeholder:text-slate-600 focus:border-cyan-500/60 disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={busy || (input.trim() === "" && attached === null)}
            className="rounded bg-cyan-500 px-3 py-1.5 text-sm font-semibold text-ink transition hover:bg-cyan-400 disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </form>
    </section>
  );
}
