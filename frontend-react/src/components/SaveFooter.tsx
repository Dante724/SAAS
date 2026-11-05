import Button from "./Button";

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
    // Save first, on the left (the app's rule for footers), then Discard and the status line.
    <div className={`flex flex-wrap items-center gap-2 px-6 py-3.5 ${className}`}>
      <Button variant="primary" onClick={onSave} disabled={busy || !dirty}>{busy ? "Saving…" : saveLabel}</Button>
      {dirty && onDiscard && <Button variant="ghost" onClick={onDiscard}>Discard</Button>}
      <span className="text-2xs text-ink-tertiary">{dirty ? "Unsaved changes" : ""}</span>
      {msg && <span role="status" className={`text-xs ${msg.ok ? "text-emerald-700" : "text-rose-600"}`}>{msg.text}</span>}
    </div>
  );
}
