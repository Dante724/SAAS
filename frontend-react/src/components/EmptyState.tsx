import type { ReactNode } from "react";
import Button from "./Button";

// The app's one "nothing here" message: an icon, a short title, one line, and at most one button.
// Never repeat the page's own main button here; the empty state points to the next step.
// Sizes: page (a whole empty page), section (a card or table), inline (one quiet line).
export default function EmptyState({ icon, title, body, action, size = "section", className = "" }: {
  icon?: ReactNode;
  title: ReactNode;
  body?: ReactNode;
  action?: ReactNode;
  size?: "page" | "section" | "inline";
  className?: string;
}) {
  if (size === "inline") return <p className={`text-xs text-ink-tertiary py-3 ${className}`}>{title}</p>;
  return (
    <div className={`text-center px-4 ${size === "page" ? "py-16" : "py-10"} ${className}`}>
      {icon && <div className="mx-auto mb-3 h-10 w-10 rounded-full bg-slate-100 text-ink-tertiary grid place-items-center">{icon}</div>}
      <p className="text-sm font-semibold text-ink-primary">{title}</p>
      {body && <p className="mt-1 text-xs text-ink-tertiary max-w-sm mx-auto">{body}</p>}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}

// The empty message as a table row, spanning every column (it stays readable on phones: see the
// table rule in index.css).
export function TableEmpty({ cols, ...props }: { cols: number } & Parameters<typeof EmptyState>[0]) {
  return <tr><td colSpan={cols} className="p-0"><EmptyState {...props} /></td></tr>;
}

// Something failed to load: say what, and offer the next step.
export function ErrorState({ title = "This didn't load", body = "Check your connection and try again.", onRetry, size = "section" }: {
  title?: ReactNode;
  body?: ReactNode;
  onRetry?: () => void;
  size?: "page" | "section";
}) {
  return (
    <div role="alert">
      <EmptyState size={size} title={title} body={body} action={onRetry && <Button onClick={onRetry}>Try again</Button>} />
    </div>
  );
}
