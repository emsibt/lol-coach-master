export default function Landing() {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="max-w-3xl">
        <p className="mb-2 font-mono text-xs uppercase tracking-[0.3em] text-cyan-400">
          neurosymbolic drafting
        </p>
        <h1 className="text-4xl font-bold tracking-tight text-slate-50 sm:text-5xl">LoL Coach</h1>
        <p className="mt-4 max-w-2xl text-lg text-slate-300">
          A deterministic engine reads an OP.GG-derived knowledge graph and returns exactly one
          counter-pick and one synergy-pick — every point backed by a source-tagged trace.
        </p>

        <div className="mt-8 grid gap-3 sm:grid-cols-3">
          {[
            ["Engine decides", "Pure Python scores the board; no model in the decision path."],
            ["Data explains", "Each pick cites measured win rates and sample sizes."],
            ["LLM narrates", "The narrator rephrases traces and may add no new facts."],
          ].map(([title, body]) => (
            <div key={title} className="rounded-lg border border-edge bg-panel/60 p-4">
              <h2 className="mb-1 text-sm font-semibold text-slate-100">{title}</h2>
              <p className="text-sm text-slate-400">{body}</p>
            </div>
          ))}
        </div>

        <p className="mt-6 rounded border border-cyan-500/30 bg-cyan-500/5 p-3 text-sm text-cyan-200">
          Invariant: the LLM never decides picks.
        </p>

        <a
          href="/app"
          className="mt-8 inline-block rounded bg-cyan-500 px-5 py-2.5 font-semibold text-ink transition hover:bg-cyan-400"
        >
          Open the draft coach →
        </a>
      </div>
    </div>
  );
}
