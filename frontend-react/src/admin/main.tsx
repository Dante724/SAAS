import React from "react";
import ReactDOM from "react-dom/client";
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "../index.css";
import AdminApp from "./AdminApp";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><AdminApp /></React.StrictMode>,
);
