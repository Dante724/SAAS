import { M } from "./Shell";
import { printHtmlInFrame } from "../lib/safeHtml";

// Settings › Documents you print: how to turn the browser's print dialog off, so Print (and
// everything switched on to print by itself) comes straight out of the printer — any printer this
// computer already prints to, wired or wireless. Browsers only allow this as a setting on the
// computer itself, so the card shows the steps for the browser in use, plus a test print.
export type Browser = "chrome" | "edge" | "firefox" | "safari" | "other";

export function detectBrowser(ua: string = typeof navigator !== "undefined" ? navigator.userAgent : ""): Browser {
  if (/Firefox\//.test(ua)) return "firefox";
  if (/Edg\//.test(ua)) return "edge";
  if (/Chrome\//.test(ua)) return "chrome";
  if (/Safari\//.test(ua)) return "safari";
  return "other";
}

const TEST_PAGE = `<!doctype html><html><head><meta charset="utf-8"><style>
@page{size:auto;margin:6mm}body{font-family:Arial,sans-serif;font-size:12px;color:#111;margin:0}
.ruler{width:50mm;height:4mm;border:1px solid #111;border-top:0;background:repeating-linear-gradient(90deg,#111 0 0.3mm,transparent 0.3mm 5mm)}
</style></head><body><p><strong>Tekly test print</strong></p>
<p>If this came out without a print window, one-click printing is on.</p>
<div class="ruler"></div><p style="font-size:10px">This bar is 50 mm wide. If it measures differently, check the printer's paper size and scaling (100%, no "fit to page").</p>
</body></html>`;

function Steps({ browser }: { browser: Browser }) {
  if (browser === "firefox") return (
    <ol className="list-decimal pl-4 space-y-1">
      <li>Print the test page below once, and pick the printer you want in the print window.</li>
      <li>In the address bar type <code className="px-1 rounded bg-slate-100">about:config</code> and accept the warning.</li>
      <li>Search <code className="px-1 rounded bg-slate-100">print.always_print_silent</code> and set it to <strong>true</strong> (add it as a Boolean if it isn't there).</li>
      <li>Print the test page again: it goes straight to that printer.</li>
    </ol>
  );
  if (browser === "chrome" || browser === "edge") {
    const name = browser === "edge" ? "Microsoft Edge" : "Google Chrome";
    return (
      <ol className="list-decimal pl-4 space-y-1">
        <li>Make the printer you want this computer's default printer.</li>
        <li><strong>Windows:</strong> right-click the {name} shortcut you open Tekly with, choose Properties, and at the end of Target add <code className="px-1 rounded bg-slate-100">--kiosk-printing</code> (after a space).</li>
        <li><strong>Mac:</strong> quit {name}, then open it from Terminal with <code className="px-1 rounded bg-slate-100">open -a "{name}" --args --kiosk-printing</code>.</li>
        <li>Open Tekly from that shortcut and print the test page: no print window.</li>
      </ol>
    );
  }
  return (
    <p>Safari can't print without its print window. Use Chrome, Edge or Firefox on the counter computer, then open this page there for the steps.</p>
  );
}

export default function PrintSetupCard() {
  const browser = detectBrowser();
  return (
    <details className="rounded-2xl border border-slate-200/90 bg-white shadow-clay-card px-4 py-3 text-xs text-ink-secondary">
      <summary className="cursor-pointer flex items-center gap-2 text-sm font-semibold text-ink-primary">
        <M n="print" c="text-base text-primary" />Turn on one-click printing
        <span className="text-[11px] font-normal text-ink-tertiary">Print without the print window, on any printer this computer uses</span>
      </summary>
      <div className="mt-3 space-y-3">
        <Steps browser={browser} />
        <p className="text-[11px] text-ink-tertiary">It's set once per computer. The browser prints to one printer this way; to send labels and receipts to different printers, use a separate computer or browser profile for each.</p>
        <button type="button" onClick={() => printHtmlInFrame(TEST_PAGE)}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-semibold text-ink-primary hover:bg-slate-50">
          <M n="print" c="text-sm" />Print a test page</button>
      </div>
    </details>
  );
}
