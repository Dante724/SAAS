// The Tekly mark: a hex nut with softened, machined corners and a chisel-cut lowercase "t"
// through it — repair, and the name, in one solid shape. Monochrome by design: it takes the text colour (near-black in light,
// near-white in dark). The same drawing ships as /static/icons/tekly-mark.svg (favicon)
// and the PWA icons, so keep them in step.

const HEX = "M26.52 6.66Q32.00 3.50 37.48 6.66L51.20 14.59Q56.68 17.75 56.68 24.07L56.68 39.92Q56.68 46.25 51.20 49.41L37.48 57.34Q32.00 60.50 26.52 57.34L12.80 49.41Q7.32 46.25 7.32 39.93L7.32 24.07Q7.32 17.75 12.80 14.59ZM27.5 17L35.5 13V24H43V31.5H35.5V41.5C35.5 43.6 36.6 44.5 38.5 44.5H43V52H37.5C31 52 27.5 48.5 27.5 42.5V31.5H20.5V24H27.5Z";

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
