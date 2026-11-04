import { forwardRef, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";

// The app's one field look: 36px tall, 12px corners, the glass-input border and focus ring.
// fieldClass() is for the odd native control that can't use Input/Select (a file or month input).
export const fieldClass = (extra = "") => `glass-input w-full rounded-xl h-9 px-3 text-sm text-ink-primary disabled:opacity-60 ${extra}`;
export const labelClass = "block text-xs font-semibold text-ink-secondary mb-1.5";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className = "", ...rest }, ref) {
  return <input ref={ref} className={fieldClass(className)} {...rest} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className = "", ...rest }, ref) {
  return <select ref={ref} className={fieldClass(`pr-8 ${className}`)} {...rest} />;
});

// A labelled field: label above, the control, then a hint or an error under it.
export function Field({ label, htmlFor, hint, error, children, className = "" }: {
  label: ReactNode;
  htmlFor?: string;
  hint?: ReactNode;
  error?: string | null;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <label htmlFor={htmlFor} className={labelClass}>{label}</label>
      {children}
      {error ? <p role="alert" className="mt-1 text-xs text-rose-600">{error}</p> : hint ? <p className="mt-1 text-xs text-ink-tertiary">{hint}</p> : null}
    </div>
  );
}
