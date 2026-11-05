import { Link } from "react-router-dom";
import Shell, { M } from "../components/Shell";

// Any address the app doesn't know (an old bookmark, a mistyped link). It sits inside the normal
// app frame, so the menu, the header and dark mode are all there and nothing looks broken.
export default function NotFound() {
  return (
    <Shell title="Page not found">
      <div className="flex-1 overflow-y-auto grid place-items-center p-10 text-center">
        <div>
          <M n="search_off" c="text-3xl text-ink-tertiary" />
          <div className="mt-2 text-sm font-semibold text-ink-primary">This page doesn't exist</div>
          <div className="text-xs text-ink-tertiary mt-1">The link may be old or mistyped.</div>
          <Link to="/" className="inline-flex items-center mt-4 h-9 px-3.5 rounded-xl bg-primary text-white text-xs font-semibold hover:opacity-90">Go to Home</Link>
        </div>
      </div>
    </Shell>
  );
}
