import { useEffect, useState } from "react";

import Agents from "./components/Agents";
import Evaluation from "./components/Evaluation";
import TabButton from "./components/TabButton";

// The maintenance-agent tooling is served from its own routes under /admin so it
// stays out of the coach application. Routing is a plain pathname check (the app
// has no router), so a tab is just a path.
type AdminTab = "agents" | "evaluation";

const TABS: { id: AdminTab; label: string; path: string }[] = [
  { id: "agents", label: "Agent runs", path: "/admin/agents" },
  { id: "evaluation", label: "Evaluation", path: "/admin/evaluation" },
];

function tabFromPath(pathname: string): AdminTab {
  return TABS.find((tab) => pathname.startsWith(tab.path))?.id ?? "agents";
}

export default function Admin() {
  const [tab, setTab] = useState<AdminTab>(() => tabFromPath(window.location.pathname));
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const onPopState = () => setTab(tabFromPath(window.location.pathname));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const go = (next: AdminTab) => {
    const path = TABS.find((item) => item.id === next)?.path ?? "/admin/agents";
    if (window.location.pathname !== path) window.history.pushState(null, "", path);
    setTab(next);
  };

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-edge bg-panel/50 px-4 py-3">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-3">
          <span className="text-lg font-bold text-slate-100">LoL Coach</span>
          <span className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-amber-300">
            Admin
          </span>
          <nav className="flex gap-1">
            {TABS.map((item) => (
              <TabButton key={item.id} active={tab === item.id} onClick={() => go(item.id)}>
                {item.label}
              </TabButton>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <button
              type="button"
              onClick={() => setRefreshKey((key) => key + 1)}
              className="rounded border border-edge bg-panel/60 px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:border-cyan-500/70 hover:text-cyan-200"
            >
              Refresh
            </button>
            <a href="/app" className="text-xs text-slate-400 hover:text-slate-200">
              Coach app
            </a>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1400px] flex-1 p-4">
        {tab === "agents" ? <Agents refreshKey={refreshKey} /> : <Evaluation refreshKey={refreshKey} />}
      </main>
    </div>
  );
}
