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
import "../index.css";

applyTheme();
watchSystemTheme();

if (!hasToken()) {
  window.location.replace(LOGIN_URL);
} else {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/go/sw.js", { scope: "/go/" }).catch(() => {});
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
