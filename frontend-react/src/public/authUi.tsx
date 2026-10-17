import { useEffect, type ReactNode } from "react";
import { Loader2, Wrench } from "lucide-react";

// Shared look for every signed-out page (sign in, sign up, reset password, verify email,
// accept invite): the real app mark, one centred column, Apple-style floating labels, one solid
// button, a quiet footer. Every colour is a theme token, so pages are wholly light or wholly dark
// with the device — nothing here is fixed to one theme.

export function useDeviceTheme() {
  useEffect(() => {
    try {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const set = () => document.documentElement.classList.toggle("dark", mq.matches);
      set(); mq.addEventListener("change", set);
    } catch { /* ignore */ }
  }, []);
}

export function Mark({ size = 56 }: { size?: number }) {
  return (
    <span className="grid place-items-center rounded-2xl bg-primary text-white" style={{ width: size, height: size }}>
      <Wrench size={Math.round(size / 2)} strokeWidth={2} />
    </span>
  );
}

export function AuthShell({ title, sub, icon, children, wide = false }: {
  title: string; sub?: ReactNode; icon?: ReactNode; children: ReactNode; wide?: boolean;
}) {
  useDeviceTheme();
  return (
    <div className="flex min-h-screen flex-col bg-white font-sans text-ink-primary">
      <header className="flex h-16 items-center px-6 sm:px-10">
        <a href="/login" className="flex items-center gap-2.5" aria-label="RepairOS">
          <span className="grid h-8 w-8 place-items-center rounded-[10px] bg-primary text-white"><Wrench size={17} strokeWidth={2.2} /></span>
          <span className="text-[17px] font-semibold tracking-[-.2px]">RepairOS</span>
        </a>
      </header>
      <main className="flex flex-1 items-start justify-center px-6 pb-10 pt-[8vh] sm:pt-[11vh]">
        <div className={`w-full ${wide ? "max-w-[460px]" : "max-w-[380px]"}`}>
          <div className="flex justify-center">{icon ?? <Mark />}</div>
          <h1 className="mt-6 text-center text-[30px] font-semibold leading-tight tracking-[-.6px]">{title}</h1>
          {sub && <p className="mt-2 text-center text-[15px] text-ink-secondary">{sub}</p>}
          <div className="mt-9">{children}</div>
        </div>
      </main>
      <footer className="px-6 py-6 text-center text-[12px] text-ink-tertiary">© {new Date().getFullYear()} RepairOS</footer>
    </div>
  );
}

// The label sits inside the box and floats up once the field has focus or a value. Wrap an
// <input className={inputCls} placeholder=" " id=…> or a <select className={selectCls} id=…>.
export function Field({ id, label, children, right, select = false }: {
  id: string; label: string; children: ReactNode; right?: ReactNode; select?: boolean;
}) {
  const float = select
    ? "top-[13px] text-[11px] font-medium"
    : `top-1/2 -translate-y-1/2 text-[15px]
       peer-focus:top-[13px] peer-focus:translate-y-0 peer-focus:text-[11px] peer-focus:font-medium
       peer-[:not(:placeholder-shown)]:top-[13px] peer-[:not(:placeholder-shown)]:translate-y-0 peer-[:not(:placeholder-shown)]:text-[11px] peer-[:not(:placeholder-shown)]:font-medium`;
  return (
    <div className="relative">
      {children}
      <label htmlFor={id} className={`pointer-events-none absolute left-4 text-ink-tertiary transition-all duration-150 ${float}`}>{label}</label>
      {right}
    </div>
  );
}
export const inputCls = "peer h-[58px] w-full rounded-xl border border-slate-300 bg-white px-4 pb-2 pt-6 text-[16px] text-ink-primary outline-none transition focus:border-primary focus:ring-[3px] focus:ring-primary/20";
export const selectCls = `${inputCls} appearance-none pr-10`;

export function Submit({ busy, children, busyText }: { busy: boolean; children: ReactNode; busyText: string }) {
  return (
    <button type="submit" disabled={busy}
      className="mt-6 flex h-[52px] w-full items-center justify-center gap-2 rounded-xl bg-primary text-[16px] font-semibold text-white transition hover:bg-primary-container active:scale-[.995] disabled:opacity-60">
      {busy && <Loader2 size={18} className="animate-spin" />}{busy ? busyText : children}
    </button>
  );
}

export function Notice({ err, ok }: { err?: string | null; ok?: string | null }) {
  if (err) return <p role="alert" className="mt-3 px-1 text-[14px] text-rose-600">{err}</p>;
  if (ok) return <p role="status" className="mt-3 px-1 text-[14px] text-emerald-600">{ok}</p>;
  return null;
}

export function FootLink({ children }: { children: ReactNode }) {
  return <div className="mt-8 border-t border-slate-200 pt-6 text-center text-[14px] text-ink-secondary">{children}</div>;
}
