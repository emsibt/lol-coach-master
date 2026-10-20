export default function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded px-3 py-1.5 text-sm font-medium transition ${
        active ? "bg-cyan-500/15 text-cyan-300" : "text-slate-400 hover:text-slate-200"
      }`}
    >
      {children}
    </button>
  );
}
