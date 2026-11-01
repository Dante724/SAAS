// The app's one save bar for a form or settings card: "Unsaved changes", Discard, Save.
// Put it at the bottom of the card (inside it). Save is disabled until something changed.
export default function SaveFooter({ dirty, busy = false, onSave, onDiscard, msg, saveLabel = "Save", className = "" }: {
  dirty: boolean;
  busy?: boolean;
  onSave: () => void;
  onDiscard?: () => void;
  msg?: { ok: boolean; text: string } | null;
  saveLabel?: string;
  className?: string;
}) {
  return (
    <div className={`flex items-center gap-2 px-6 py-3.5 ${className}`}>
      {msg && <span role="status" className={`text-xs ${msg.ok ? "text-emerald-700" : "text-rose-600"}`}>{msg.text}</span>}
      <span className="ml-auto text-[11px] text-ink-tertiary">{dirty ? "Unsaved changes" : ""}</span>
      {dirty && onDiscard && <button type="button" onClick={onDiscard} className="h-9 px-3 rounded-xl text-xs font-semibold text-ink-secondary hover:bg-slate-100">Discard</button>}
      <button type="button" onClick={onSave} disabled={busy || !dirty} className="h-9 px-5 rounded-xl bg-ink-primary text-white font-semibold text-xs hover:opacity-90 disabled:opacity-40">{busy ? "Saving…" : saveLabel}</button>
    </div>
  );
}
