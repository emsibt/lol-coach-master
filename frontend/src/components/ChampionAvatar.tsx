import { useEffect, useState } from "react";

import { championIconUrl, useDdragonVersion } from "../ddragon";

type Size = "sm" | "md" | "lg";

const SIZES: Record<Size, string> = {
  sm: "h-6 w-6 text-[10px]",
  md: "h-8 w-8 text-xs",
  lg: "h-12 w-12 text-sm",
};

interface Props {
  name: string;
  championKey: string;
  size?: Size;
}

// A champion portrait from Data Dragon, falling back to an initial when the key
// or version is missing, or the image 404s.
export default function ChampionAvatar({ name, championKey, size = "md" }: Props) {
  const version = useDdragonVersion();
  const url = championIconUrl(championKey, version);
  const [failed, setFailed] = useState(false);

  useEffect(() => setFailed(false), [url]);

  const dimensions = SIZES[size];
  if (url === null || failed) {
    return (
      <span
        className={`inline-flex shrink-0 items-center justify-center rounded-full border border-edge bg-ink/70 font-semibold uppercase text-slate-500 ${dimensions}`}
        aria-hidden="true"
      >
        {name.slice(0, 1)}
      </span>
    );
  }
  return (
    <img
      src={url}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
      className={`shrink-0 rounded-full border border-edge bg-ink/70 object-cover ${dimensions}`}
    />
  );
}
