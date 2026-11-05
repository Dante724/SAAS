// A "Send …" button with a small menu to pick how it goes: the main click sends the default
// way (whatever the customer can get), the arrow offers Email or Text. The menu is portaled
// so a table or card with overflow-hidden can't clip it.
import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { M } from "./Shell";
import { buttonClass } from "./Button";

export type SendWay = "auto" | "email" | "sms";

const NAMES: Record<SendWay, string> = { auto: "The usual way", email: "By email", sms: "By text" };

export default function SendMenu({ label, busy, onSend, primary = true, small, ways = ["email", "sms"] }: {
  label: string; busy?: boolean; onSend: (way?: SendWay) => void; primary?: boolean; small?: boolean; ways?: SendWay[];
}) {
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const caret = useRef<HTMLButtonElement>(null);
  // One split button on the shared Button look: same height (32 small / 36), weight and colours as
  // every other button; the caret is joined to it by a hairline.
  const variant = primary ? "primary" : "secondary";
  const size = small ? "sm" : "md";
  function toggle() {
    if (pos) { setPos(null); return; }
    const r = caret.current?.getBoundingClientRect();
    const w = 168;
    const left = r ? Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)) : 8;
    setPos({ top: r ? r.bottom + 4 : 8, left });
  }
  return (
    <div className="relative inline-flex">
      <button type="button" disabled={busy} onClick={() => onSend(undefined)}
        className={buttonClass(variant, size, "rounded-r-none")}>
        <M n="send" c="text-xs" />{busy ? "Sending…" : label}
      </button>
      <button ref={caret} type="button" disabled={busy} aria-label={`${label}: choose email or text`} aria-haspopup="menu" aria-expanded={!!pos}
        onClick={toggle}
        className={buttonClass(variant, size, `rounded-l-none !px-1.5 ${primary ? "border-l border-white/30" : "border-l-0"}`)}>
        <M n="expand_more" c="text-sm" />
      </button>
      {pos && createPortal(<>
        <div aria-hidden="true" className="fixed inset-0 z-[70]" onClick={() => setPos(null)} />
        <div role="menu" style={{ top: pos.top, left: pos.left, width: 168 }} className="fixed z-[71] bg-white border border-slate-200 rounded-xl shadow-clay-drawer p-1">
          {ways.map((w) => (
            <button key={w} role="menuitem" type="button" onClick={() => { setPos(null); onSend(w); }}
              className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-left text-xs text-ink-primary hover:bg-slate-50">
              <M n={w === "sms" ? "sms" : w === "email" ? "mail" : "send"} c="text-sm text-ink-tertiary" />{NAMES[w]}
            </button>
          ))}
        </div>
      </>, document.body)}
    </div>
  );
}
