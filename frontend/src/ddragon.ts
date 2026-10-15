import { createContext, useContext } from "react";

// Champion portraits are derived, not fetched: Data Dragon serves them at
// https://ddragon.leagueoflegends.com/cdn/<version>/img/champion/<key>.png
// where <key> is the CDN key (e.g. "Ahri", "TwistedFate") and <version> is a
// fully-qualified Data Dragon version (e.g. "14.20.1"), surfaced via /api/health.
const DDRAGON_CDN = "https://ddragon.leagueoflegends.com/cdn";

export function championIconUrl(key: string, version: string): string | null {
  if (key === "" || version === "") return null;
  return `${DDRAGON_CDN}/${version}/img/champion/${encodeURIComponent(key)}.png`;
}

// The Data Dragon version the app should use; empty until /api/health loads.
export const DdragonVersionContext = createContext<string>("");

export function useDdragonVersion(): string {
  return useContext(DdragonVersionContext);
}
