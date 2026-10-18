// The Tekly mark: a flat-top hex nut, seen face-on, with softly machined corners and a bold
// chisel-cut lowercase "t" through it — repair, and the name, in one solid shape.
// Monochrome by design: it takes the text colour (near-black in light, near-white in dark).
// The same drawing ships as /static/icons/tekly-mark.svg (favicon) and the PWA icons, so keep them in step.

const HEX = "M59.50 27.67Q62.00 32.00 59.50 36.33L49.50 53.65Q47.00 57.98 42.00 57.98L22.00 57.98Q17.00 57.98 14.50 53.65L4.50 36.33Q2.00 32.00 4.50 27.67L14.50 10.35Q17.00 6.02 22.00 6.02L42.00 6.02Q47.00 6.02 49.50 10.35ZM24.5 19.5L34.5 15V25.5H42V33H34.5V40C34.5 41.8 35.4 42.5 37.2 42.5H42V49.8H35.8C28.6 49.8 24.5 46 24.5 39.5V33H20.5V25.5H24.5Z";

export function TeklyMark({ size = 32, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" className={`shrink-0 text-ink-primary ${className}`} aria-hidden="true">
      <path fill="currentColor" fillRule="evenodd" d={HEX} />
    </svg>
  );
}

// Mark + lowercase wordmark, set in Geist Bold and tightly tracked.
export function TeklyLogo({ size = 32, className = "" }: { size?: number; className?: string }) {
  return (
    <span className={`inline-flex items-center ${className}`} style={{ gap: Math.round(size * 0.28) }}>
      <TeklyMark size={size} />
      <span className="font-bold text-ink-primary" style={{ fontSize: Math.round(size * 0.78), letterSpacing: "-0.05em", lineHeight: 1 }}>tekly</span>
    </span>
  );
}
