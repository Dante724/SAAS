import React from "react";
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { MeProvider } from "./context/Me";
import PublicTrack from "./public/PublicTrack";
import TrackLookup from "./public/TrackLookup";
import PublicAppointment from "./public/PublicAppointment";
import PublicPortal from "./public/PublicPortal";
import { publicRoute } from "./publicRoutes";
import Login from "./public/Login";
import { Signup, ResetPassword, VerifyEmail, AcceptInvite } from "./public/AuthPages";
import "./index.css";

const path = window.location.pathname;
const root = ReactDOM.createRoot(document.getElementById("root")!);

// Standalone public pages (no session, no app shell, no auth guard).
const AUTH: Record<string, JSX.Element> = {
  "/login": <Login />,
  "/signup": <Signup />,
  "/reset-password": <ResetPassword />,
  "/verify-email": <VerifyEmail />,
  "/accept-invite": <AcceptInvite />,
};

const route = publicRoute(path);
if (route.page === "track" || route.page === "trackLookup") {
  // Public customer page: /track/:token (or /track lookup). Follows the visitor's device theme.
  root.render(<React.StrictMode>{route.page === "track" ? <PublicTrack /> : <TrackLookup />}</React.StrictMode>);
} else if (route.page === "portal") {
  // Customer portal: all of one customer's jobs, behind their magic link.
  root.render(<React.StrictMode><PublicPortal token={route.token} /></React.StrictMode>);
} else if (route.page === "book") {
  // Public online appointment — standalone, no session, follows device theme
  // (PublicAppointment sets the dark class itself).
  root.render(<React.StrictMode><PublicAppointment /></React.StrictMode>);
} else if (route.page === "auth") {
  // React auth pages (sign-in, signup, reset, verify, invite).
  root.render(<React.StrictMode>{AUTH[route.path]}</React.StrictMode>);
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
