// The Tekly mark: a hex nut with a lowercase "t" cut through it — repair, and the name, in
// one solid shape. Monochrome by design: it takes the text colour (near-black in light,
// near-white in dark). The same drawing ships as /static/icons/tekly-mark.svg (favicon)
// and the PWA icons, so keep them in step.

const HEX = "M32 4l24.2 14v28L32 60 7.8 46V18zm-4.5 12v8.5H21v8h6.5v10.2c0 6 3.6 9.3 9.5 9.3H43v-8h-4.6c-1.9 0-2.9-.9-2.9-2.9V32.5H43v-8h-7.5V16z";

export function TeklyMark({ size = 32, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" className={`shrink-0 text-ink-primary ${className}`} aria-hidden="true">
      <path fill="currentColor" fillRule="evenodd" d={HEX} />
    </svg>
  );
}

// Mark + lowercase wordmark, set in Geist and tightly tracked.
export function TeklyLogo({ size = 32, className = "" }: { size?: number; className?: string }) {
  return (
    <span className={`inline-flex items-center ${className}`} style={{ gap: Math.round(size * 0.28) }}>
      <TeklyMark size={size} />
      <span className="font-semibold text-ink-primary" style={{ fontSize: Math.round(size * 0.78), letterSpacing: "-0.045em", lineHeight: 1 }}>tekly</span>
    </span>
  );
}
