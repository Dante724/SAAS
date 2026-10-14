import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { MeProvider } from "./context/Me";
import PublicTrack from "./public/PublicTrack";
import TrackLookup from "./public/TrackLookup";
import PublicAppointment from "./public/PublicAppointment";
import Login from "./public/Login";
import { Signup, ResetPassword, VerifyEmail, AcceptInvite } from "./public/AuthPages";
import "./index.css";

const path = window.location.pathname;
const root = ReactDOM.createRoot(document.getElementById("root")!);

// Standalone public pages (no session, no app shell, no auth guard).
const PUBLIC: Record<string, JSX.Element> = {
  "/login": <Login />,
  "/signup": <Signup />,
  "/reset-password": <ResetPassword />,
  "/verify-email": <VerifyEmail />,
  "/accept-invite": <AcceptInvite />,
};

// ── Public customer page: /track/:token (or /track lookup) ─────────────────
// No session, no app shell, no auth guard. Follows the visitor's device theme.
const trackToken = path.startsWith("/track/") ? path.slice("/track/".length).replace(/\/$/, "") : "";
if (path === "/track" || path === "/track/" || path.startsWith("/track/")) {
  root.render(<React.StrictMode>{trackToken ? <PublicTrack /> : <TrackLookup />}</React.StrictMode>);
} else if (path.startsWith("/book/")) {
  // Public online appointment — standalone, no session, follows device theme
  // (PublicAppointment sets the dark class itself).
  root.render(<React.StrictMode><PublicAppointment /></React.StrictMode>);
} else if (PUBLIC[path]) {
  // React auth pages (sign-in, signup, reset, verify, invite).
  root.render(<React.StrictMode>{PUBLIC[path]}</React.StrictMode>);
} else {
  // Guarded cutover: the redesign is the app and is served at "/". If there's no
  // session token we send the visitor to the React /login page.
  try {
    const hasToken =
      localStorage.getItem("ros_tok") ||
      localStorage.getItem("ros_token") ||
      localStorage.getItem("token");
    if (!hasToken && path !== "/login") {
      window.location.replace("/login");
    }
  } catch { /* ignore storage errors */ }

  root.render(
    <React.StrictMode>
      <BrowserRouter basename="/">
        <MeProvider>
          <App />
        </MeProvider>
      </BrowserRouter>
    </React.StrictMode>
  );
}
