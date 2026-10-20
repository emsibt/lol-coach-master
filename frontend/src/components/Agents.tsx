import { useEffect, useMemo, useState } from "react";

import { api } from "../api";
import type { ExecutionDetail, ExecutionSummary } from "../types";
import { StatusBadge } from "./Badges";
import TraceGraph from "./TraceGraph";

const AGENT_WORKFLOWS = ["issue-fix", "pr-review"];

interface Props {
  refreshKey: number;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function text(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  return String(value);
}

function strList(value: unknown): string[] {
  return asArray(value).map(text).filter((item) => item !== "");
}

function num(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function Chip({ children, tone = "slate" }: { children: React.ReactNode; tone?: string }) {
  const tones: Record<string, string> = {
    slate: "border-slate-500/40 bg-slate-500/10 text-slate-300",
    emerald: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
    rose: "border-rose-500/40 bg-rose-500/10 text-rose-300",
    cyan: "border-cyan-500/40 bg-cyan-500/10 text-cyan-300",
  };
  return (
    <span className={`inline-block rounded border px-2 py-0.5 text-[11px] font-medium ${tones[tone] ?? tones.slate}`}>
      {children}
    </span>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-edge bg-panel/40 p-3">
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{title}</h3>
      {children}
    </div>
  );
}

function Bullets({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1">
      {items.map((item, index) => (
        <li key={`${index}-${item}`} className="flex gap-2 text-xs text-slate-400">
          <span className="text-slate-600">•</span>
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

function FileList({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1">
      {items.map((path) => (
        <span key={path} className="rounded bg-ink/70 px-1.5 py-0.5 font-mono text-[11px] text-slate-400">
          {path}
        </span>
      ))}
    </div>
  );
}

function InvestigationPanel({ data }: { data: Record<string, unknown> }) {
  if (Object.keys(data).length === 0) return null;
  const confidence = num(data.confidence);
  return (
    <Section title="Investigation">
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone={data.is_real_issue === true ? "emerald" : "rose"}>
          {data.is_real_issue === true ? "real issue" : "not actionable"}
        </Chip>
        {data.reproduces === true && <Chip tone="cyan">reproduced</Chip>}
        {confidence !== null && <Chip>confidence {(confidence * 100).toFixed(0)}%</Chip>}
      </div>
      {text(data.summary) !== "" && <p className="mt-2 text-sm text-slate-300">{text(data.summary)}</p>}
      <Bullets items={strList(data.findings)} />
      <FileList items={strList(data.files)} />
    </Section>
  );
}

function PlanPanel({ data }: { data: Record<string, unknown> }) {
  if (Object.keys(data).length === 0) return null;
  const steps = strList(data.steps);
  return (
    <Section title="Plan">
      {text(data.summary) !== "" && <p className="text-sm text-slate-300">{text(data.summary)}</p>}
      {steps.length > 0 && (
        <ol className="mt-2 space-y-1">
          {steps.map((step, index) => (
            <li key={`${index}-${step}`} className="flex gap-2 text-xs text-slate-400">
              <span className="font-mono text-slate-600">{index + 1}.</span>
              <span>{step}</span>
            </li>
          ))}
        </ol>
      )}
      <FileList items={strList(data.files)} />
      {text(data.test_plan) !== "" && (
        <p className="mt-2 font-mono text-[11px] text-slate-500">{text(data.test_plan)}</p>
      )}
    </Section>
  );
}

function ChangePanel({ data }: { data: Record<string, unknown> }) {
  if (Object.keys(data).length === 0) return null;
  return (
    <Section title="Change">
      {text(data.summary) !== "" && <p className="text-sm text-slate-300">{text(data.summary)}</p>}
      <FileList items={strList(data.files_changed)} />
      <Bullets items={strList(data.notes)} />
    </Section>
  );
}

function VerificationPanel({ data }: { data: Record<string, unknown> }) {
  const commands = asArray(data.commands).map(asRecord);
  if (commands.length === 0) return null;
  return (
    <Section title="Verification">
      <div className="mb-2">
        <Chip tone={data.passed === true ? "emerald" : "rose"}>
          {data.passed === true ? "passed" : "failed"}
        </Chip>
      </div>
      <ul className="space-y-1">
        {commands.map((entry, index) => (
          <li key={`${index}-${text(entry.command)}`} className="flex items-center gap-2 text-xs">
            <span className={num(entry.exit_code) === 0 ? "text-emerald-300" : "text-rose-300"}>
              exit {text(entry.exit_code)}
            </span>
            <span className="truncate font-mono text-slate-400">{text(entry.command)}</span>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function PullRequestPanel({ data }: { data: Record<string, unknown> }) {
  if (Object.keys(data).length === 0) return null;
  const url = text(data.url);
  return (
    <Section title="Pull request">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm text-cyan-300">#{text(data.number)}</span>
        <span className="truncate text-sm text-slate-300">{text(data.title)}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
        <span className="font-mono">
          {text(data.head)} → {text(data.base)}
        </span>
        {url !== "" && (
          <a href={url} target="_blank" rel="noreferrer" className="text-cyan-300 hover:underline">
            open on GitHub
          </a>
        )}
      </div>
    </Section>
  );
}

function ReviewPanel({ data, merged }: { data: Record<string, unknown>; merged: unknown }) {
  if (Object.keys(data).length === 0) return null;
  const approved = text(data.decision) === "approve";
  const checks = asArray(data.checks).map(asRecord);
  const comments = asArray(data.comments).map(asRecord);
  return (
    <Section title="Review verdict">
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone={approved ? "emerald" : "rose"}>{approved ? "approved" : "changes requested"}</Chip>
        {merged === true ? <Chip tone="cyan">merged by the agent</Chip> : <Chip>not merged (a human merges)</Chip>}
      </div>
      {text(data.summary) !== "" && <p className="mt-2 text-sm text-slate-300">{text(data.summary)}</p>}
      {checks.length > 0 && (
        <ul className="mt-2 space-y-1">
          {checks.map((check, index) => (
            <li key={`${index}-${text(check.name)}`} className="flex gap-2 text-xs">
              <span className={check.ok === true ? "text-emerald-300" : "text-rose-300"}>
                {check.ok === true ? "[x]" : "[ ]"}
              </span>
              <span className="text-slate-300">{text(check.name)}</span>
              <span className="text-slate-500">{text(check.detail)}</span>
            </li>
          ))}
        </ul>
      )}
      {comments.length > 0 && (
        <ul className="mt-2 space-y-1">
          {comments.map((comment, index) => (
            <li key={`${index}-${text(comment.body)}`} className="text-xs text-slate-400">
              <span className="font-mono text-slate-500">
                {text(comment.path)}
                {text(comment.line) !== "" && text(comment.line) !== "0" ? `:${text(comment.line)}` : ""}
              </span>{" "}
              {text(comment.body)}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function AgentRunPanel({ detail }: { detail: ExecutionDetail }) {
  const output = asRecord(detail.output);
  const isFix = detail.workflow === "issue-fix";
  const branch = text(output.branch);
  return (
    <div className="rounded-lg border border-edge bg-panel/60 p-3">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-slate-100">
          {isFix ? "Issue-fix run" : "PR-review run"}
        </h2>
        <StatusBadge status={detail.status} />
        {branch !== "" && <Chip tone="cyan">{branch}</Chip>}
        {text(output.summary) !== "" && (
          <span className="truncate text-xs text-slate-400">{text(output.summary)}</span>
        )}
        <span className="ml-auto font-mono text-[11px] text-slate-500">
          {detail.execution_id.slice(0, 8)}
        </span>
      </div>

      {detail.error !== null && (
        <p className="mb-3 rounded border border-rose-500/40 bg-rose-500/10 p-2 text-xs text-rose-200">
          {detail.error}
        </p>
      )}

      {isFix ? (
        <div className="grid gap-3 md:grid-cols-2">
          <InvestigationPanel data={asRecord(output.investigation)} />
          <PlanPanel data={asRecord(output.plan)} />
          <ChangePanel data={asRecord(output.implementation)} />
          <VerificationPanel data={asRecord(output.verification)} />
          <PullRequestPanel data={asRecord(output.pull_request)} />
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          <PullRequestPanel data={asRecord(output.pull_request)} />
          <ReviewPanel data={asRecord(output.review)} merged={output.merged} />
        </div>
      )}
    </div>
  );
}

function RunList({
  rows,
  selectedId,
  onSelect,
}: {
  rows: ExecutionSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (rows.length === 0) {
    return (
      <p className="px-1 text-sm text-slate-500">
        No agent runs yet. Trigger one with <code className="text-slate-400">python -m lolcoach.cli agent-fix --all</code>.
      </p>
    );
  }
  return (
    <ul className="space-y-1.5">
      {rows.map((row) => (
        <li key={row.execution_id}>
          <button
            type="button"
            onClick={() => onSelect(row.execution_id)}
            className={`w-full rounded-lg border px-3 py-2 text-left transition ${
              row.execution_id === selectedId
                ? "border-cyan-500/70 bg-cyan-500/10"
                : "border-edge bg-panel/50 hover:border-slate-500"
            }`}
          >
            <div className="mb-1 flex items-center justify-between gap-2">
              <span className="truncate text-xs font-medium text-slate-300">{row.workflow}</span>
              <StatusBadge status={row.status} />
            </div>
            <div className="flex items-center justify-between text-[11px] text-slate-500">
              <span className="truncate">{row.started_at.replace("T", " ").slice(0, 19)}</span>
              <span className="ml-2 shrink-0 font-mono">{row.execution_id.slice(0, 8)}</span>
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}

export default function Agents({ refreshKey }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [summaries, setSummaries] = useState<ExecutionSummary[]>([]);
  const [detail, setDetail] = useState<ExecutionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [workflowFilter, setWorkflowFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");

  useEffect(() => {
    let alive = true;
    Promise.all(AGENT_WORKFLOWS.map((workflow) => api.adminExecutions(200, workflow)))
      .then((pages) => {
        if (!alive) return;
        const agents = pages
          .flat()
          .sort((left, right) => right.started_at.localeCompare(left.started_at));
        setSummaries(agents);
        setError(null);
        setSelectedId((current) =>
          current !== null && agents.some((row) => row.execution_id === current)
            ? current
            : (agents[0]?.execution_id ?? null),
        );
      })
      .catch((cause: unknown) => {
        if (alive) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      alive = false;
    };
  }, [refreshKey]);

  useEffect(() => {
    if (selectedId === null) {
      setDetail(null);
      return;
    }
    let alive = true;
    api
      .adminExecution(selectedId)
      .then((record) => {
        if (alive) setDetail(record);
      })
      .catch((cause: unknown) => {
        if (alive) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      alive = false;
    };
  }, [selectedId, refreshKey]);

  const filtered = useMemo(
    () =>
      summaries.filter((row) => {
        if (workflowFilter !== "all" && row.workflow !== workflowFilter) return false;
        if (statusFilter !== "all" && row.status !== statusFilter) return false;
        return true;
      }),
    [summaries, workflowFilter, statusFilter],
  );

  const selectClass =
    "rounded border border-edge bg-panel/60 px-2 py-1 text-xs text-slate-300 outline-none focus:border-cyan-500/70";

  return (
    <div className="grid h-[calc(100vh-7rem)] min-h-[32rem] gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
      <aside className="flex min-h-0 flex-col rounded-lg border border-edge bg-panel/60 p-3">
        <h2 className="mb-3 text-sm font-semibold text-slate-200">Agent runs</h2>
        <div className="mb-3 flex gap-2">
          <select
            value={workflowFilter}
            onChange={(event) => setWorkflowFilter(event.target.value)}
            className={`${selectClass} flex-1`}
          >
            <option value="all">All agents</option>
            <option value="issue-fix">issue-fix</option>
            <option value="pr-review">pr-review</option>
          </select>
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            className={`${selectClass} flex-1`}
          >
            <option value="all">All status</option>
            <option value="succeeded">Success</option>
            <option value="failed">Failed</option>
          </select>
        </div>
        {error !== null && <p className="mb-2 text-xs text-rose-300">{error}</p>}
        <div className="min-h-0 flex-1 overflow-auto pr-1">
          <RunList rows={filtered} selectedId={selectedId} onSelect={setSelectedId} />
        </div>
      </aside>

      <section className="flex min-h-0 flex-col gap-3">
        {detail === null ? (
          <div className="flex h-full items-center justify-center rounded-lg border border-edge bg-[#0c1322]">
            <p className="text-sm text-slate-500">Select an agent run to see what it investigated and changed.</p>
          </div>
        ) : (
          <>
            <div className="max-h-[45%] shrink-0 overflow-auto">
              <AgentRunPanel detail={detail} />
            </div>
            <div className="min-h-0 flex-1">
              <TraceGraph detail={detail} />
            </div>
          </>
        )}
      </section>
    </div>
  );
}
