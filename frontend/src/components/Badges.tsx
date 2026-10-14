const STATUS_STYLES: Record<string, string> = {
  succeeded: "border-emerald-500/40 bg-emerald-500/15 text-emerald-300",
  failed: "border-rose-500/40 bg-rose-500/15 text-rose-300",
  running: "border-amber-500/40 bg-amber-500/15 text-amber-300",
};

export function StatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLES[status] ?? "border-slate-500/40 bg-slate-500/15 text-slate-300";
  return (
    <span className={`inline-block rounded border px-2 py-0.5 text-xs font-medium ${style}`}>
      {status}
    </span>
  );
}

function eventStyle(type: string): string {
  if (type.includes("FAILED")) return "border-rose-500/40 bg-rose-500/15 text-rose-300";
  if (type === "EXECUTION_SUCCEEDED") return "border-emerald-500/40 bg-emerald-500/15 text-emerald-300";
  if (type.includes("SUCCEEDED")) return "border-teal-500/40 bg-teal-500/15 text-teal-300";
  if (type === "EXECUTION_STARTED") return "border-sky-500/40 bg-sky-500/15 text-sky-300";
  return "border-slate-500/40 bg-slate-500/15 text-slate-300";
}

export function EventBadge({ type }: { type: string }) {
  return (
    <span className={`inline-block rounded border px-2 py-0.5 font-mono text-[11px] font-medium ${eventStyle(type)}`}>
      {type}
    </span>
  );
}

export function KindBadge({ kind }: { kind: string }) {
  const style =
    kind === "counter"
      ? "border-cyan-500/40 bg-cyan-500/15 text-cyan-300"
      : "border-violet-500/40 bg-violet-500/15 text-violet-300";
  return (
    <span className={`inline-block rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${style}`}>
      {kind}
    </span>
  );
}
