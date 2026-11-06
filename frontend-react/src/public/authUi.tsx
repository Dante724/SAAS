import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";
import Button from "../components/Button";
import { TeklyMark } from "../components/brand/TeklyLogo";
import PublicLayout, { Field, PublicTitle, inputCls, selectCls } from "./PublicLayout";

// Shared look for every signed-out page (sign in, sign up, reset password, verify email,
// accept invite): the customer-page frame (PublicLayout: Tekly header, one width, the one footer
// credit, always light), then one centred column of one width with the mark, the title, labelled
// fields and one sapphire button.

export { Field, inputCls, selectCls };

export function Mark({ size = 56 }: { size?: number }) {
  return <TeklyMark size={size} />;
}

export function AuthShell({ title, sub, icon, children }: {
  title: string; sub?: ReactNode; icon?: ReactNode; children: ReactNode;
}) {
  return (
    <PublicLayout>
      <div className="mx-auto w-full max-w-[420px] pb-6 sm:pt-[4vh]">
        <div className="mb-6 flex justify-center">{icon ?? <Mark />}</div>
        <PublicTitle center title={title} sub={sub} />
        <div className="mt-8">{children}</div>
      </div>
    </PublicLayout>
  );
}

export function Submit({ busy, children, busyText }: { busy: boolean; children: ReactNode; busyText: string }) {
  return (
    <Button type="submit" variant="primary" size="lg" busy={busy} className="mt-6 w-full"
      icon={busy ? <Loader2 size={18} className="animate-spin" /> : undefined}>
      {busy ? busyText : children}
    </Button>
  );
}

export function Notice({ err, ok }: { err?: string | null; ok?: string | null }) {
  if (err) return <p role="alert" className="mt-3 px-1 text-sm text-rose-600">{err}</p>;
  if (ok) return <p role="status" className="mt-3 px-1 text-sm text-emerald-600">{ok}</p>;
  return null;
}

export function FootLink({ children }: { children: ReactNode }) {
  return <div className="mt-8 border-t border-slate-200 pt-6 text-center text-sm text-ink-secondary">{children}</div>;
}
