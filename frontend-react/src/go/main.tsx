// Tekly Go entry (served at /go). Reuses the web app's theme (index.css + theme tokens), API client
// and Me context; adds its own service worker scoped to /go/.
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { MeProvider } from "../context/Me";
import GoApp from "./GoApp";
import { applyTheme, watchSystemTheme } from "./theme";
import { LOGIN_URL, hasToken } from "./session";
import { goQueue } from "./lib/offlineQueue";
import { GO_CLIENT, noteLabelOpen } from "./lib/client";
import { api, setClientTag } from "../api/client";
import "../index.css";

applyTheme();
watchSystemTheme();
setClientTag(GO_CLIENT);   // every API call says it came from Tekly Go

if (!hasToken()) {
  // a job opened from a label (or any deep link) comes back to that job after signing in
  const here = window.location.pathname + window.location.search;
  window.location.replace(/^\/go\/.+/.test(here) ? "/login?next=" + encodeURIComponent(here) : LOGIN_URL);
} else {
  noteLabelOpen(window.location.href, (code) => api.post("/scan/resolve", { code }),
    (url) => window.history.replaceState(window.history.state, "", url));
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/go/sw.js", { scope: "/go/" }).catch(() => { /* silent: the app works without the service worker */ });
  }
  goQueue();   // start replaying anything left over from last time
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <BrowserRouter basename="/go">
        <MeProvider>
          <GoApp />
        </MeProvider>
      </BrowserRouter>
    </React.StrictMode>
  );
}
