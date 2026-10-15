import { createRoot } from "react-dom/client";

// Promise-based, in-app replacements for native window.confirm / alert / prompt — styled with the
// app's design tokens and non-blocking. Each mounts a transient portal and resolves on action, so a
// call site changes by ~one line:
//   if (!confirm(msg)) return;            →  if (!(await confirmDialog(msg))) return;
//   alert(msg)                            →  await alertDialog(msg);
//   const v = prompt(msg);                →  const v = await promptDialog(msg);

type ConfirmOpts = { title?: string; confirmLabel?: string; cancelLabel?: string; danger?: boolean };
type PromptOpts = { title?: string; defaultValue?: string; placeholder?: string; confirmLabel?: string };

function mount(render: (close: () => void) => any) {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const close = () => { root.unmount(); host.remove(); };
  root.render(render(close));
}

const overlay = "fixed inset-0 z-[200] flex items-center justify-center p-4";
const scrim = "absolute inset-0 bg-ink-primary/40 backdrop-blur-[2px]";
const card = "relative bg-white rounded-2xl shadow-clay-drawer border border-slate-200 w-full max-w-sm p-5 space-y-4";
const btnGhost = "px-4 py-2 rounded-xl text-xs font-bold text-ink-secondary bg-white border border-slate-200 hover:bg-slate-50";

export function confirmDialog(message: string, opts: ConfirmOpts = {}): Promise<boolean> {
  return new Promise((resolve) => {
    mount((close) => {
      const done = (v: boolean) => { close(); resolve(v); };
      return (
        <div className={overlay}>
          <div className={scrim} onClick={() => done(false)} />
          <div className={card}>
            {opts.title && <h3 className="text-sm font-black text-ink-primary">{opts.title}</h3>}
            <p className="text-xs text-ink-secondary whitespace-pre-line">{message}</p>
            <div className="flex justify-end gap-2">
              <button className={btnGhost} onClick={() => done(false)}>{opts.cancelLabel || "Cancel"}</button>
              <button autoFocus onClick={() => done(true)}
                className={`px-4 py-2 rounded-xl text-xs font-bold text-white shadow-clay-sm hover:brightness-110 ${opts.danger ? "bg-rose-600 hover:bg-rose-700" : "bg-gradient-to-r from-primary to-primary-container"}`}>
                {opts.confirmLabel || "Confirm"}</button>
            </div>
          </div>
        </div>
      );
    });
  });
}

export function alertDialog(message: string, title?: string): Promise<void> {
  return new Promise((resolve) => {
    mount((close) => {
      const done = () => { close(); resolve(); };
      return (
        <div className={overlay}>
          <div className={scrim} onClick={done} />
          <div className={card}>
            {title && <h3 className="text-sm font-black text-ink-primary">{title}</h3>}
            <p className="text-xs text-ink-secondary whitespace-pre-line">{message}</p>
            <div className="flex justify-end"><button autoFocus className="px-4 py-2 rounded-xl bg-gradient-to-r from-primary to-primary-container text-white text-xs font-bold shadow-clay-sm hover:brightness-110" onClick={done}>OK</button></div>
          </div>
        </div>
      );
    });
  });
}

export function promptDialog(message: string, opts: PromptOpts = {}): Promise<string | null> {
  return new Promise((resolve) => {
    mount((close) => {
      const done = (v: string | null) => { close(); resolve(v); };
      let val = opts.defaultValue || "";
      return (
        <div className={overlay}>
          <div className={scrim} onClick={() => done(null)} />
          <div className={card}>
            {opts.title && <h3 className="text-sm font-black text-ink-primary">{opts.title}</h3>}
            <p className="text-xs text-ink-secondary whitespace-pre-line">{message}</p>
            <input autoFocus defaultValue={val} placeholder={opts.placeholder}
              onChange={(e) => { val = e.target.value; }}
              onKeyDown={(e) => { if (e.key === "Enter") done(val); if (e.key === "Escape") done(null); }}
              className="glass-input w-full rounded-xl px-3 py-2 text-sm" />
            <div className="flex justify-end gap-2">
              <button className={btnGhost} onClick={() => done(null)}>Cancel</button>
              <button onClick={() => done(val)} className="px-4 py-2 rounded-xl bg-gradient-to-r from-primary to-primary-container text-white text-xs font-bold shadow-clay-sm hover:brightness-110">{opts.confirmLabel || "OK"}</button>
            </div>
          </div>
        </div>
      );
    });
  });
}

type SelectOpts = { title?: string; confirmLabel?: string; allowOther?: boolean; danger?: boolean };

// Pick one of `options` (plus free text when allowOther). Resolves to the chosen string,
// "" when the user confirms without choosing, or null when they back out.
export function selectDialog(message: string, options: string[], opts: SelectOpts = {}): Promise<string | null> {
  return new Promise((resolve) => {
    mount((close) => {
      const done = (v: string | null) => { close(); resolve(v); };
      let val = "";
      let other = "";
      return (
        <div className={overlay}>
          <div className={scrim} onClick={() => done(null)} />
          <div className={card}>
            {opts.title && <h3 className="text-sm font-black text-ink-primary">{opts.title}</h3>}
            <p className="text-xs text-ink-secondary whitespace-pre-line">{message}</p>
            <div className="max-h-64 overflow-y-auto space-y-1">
              {options.map((o) => (
                <label key={o} className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-slate-50 cursor-pointer text-sm text-ink-primary">
                  <input type="radio" name="sel-dialog" className="accent-primary" onChange={() => { val = o; }} />{o}
                </label>
              ))}
              {opts.allowOther && (
                <label className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-slate-50 text-sm text-ink-primary">
                  <input type="radio" name="sel-dialog" className="accent-primary" onChange={() => { val = "__other__"; }} />
                  <input placeholder="Other…" onFocus={(e) => { val = "__other__"; (e.currentTarget.previousSibling as HTMLInputElement).checked = true; }}
                    onChange={(e) => { other = e.target.value; }} className="glass-input flex-1 rounded-lg px-2 py-1 text-sm" />
                </label>
              )}
            </div>
            <div className="flex justify-end gap-2">
              <button className={btnGhost} onClick={() => done(null)}>Back</button>
              <button onClick={() => done(val === "__other__" ? other.trim() : val)}
                className={`px-4 py-2 rounded-xl text-xs font-bold text-white shadow-clay-sm hover:brightness-110 ${opts.danger ? "bg-rose-600 hover:bg-rose-700" : "bg-gradient-to-r from-primary to-primary-container"}`}>
                {opts.confirmLabel || "OK"}</button>
            </div>
          </div>
        </div>
      );
    });
  });
}
