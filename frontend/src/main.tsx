import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/tokens.css";
import { api } from "./api/client";

// Frontend errors go to the backend structured log (same file as the Python log).
window.addEventListener("error", (e) => {
  void api.feedback({ kind: "js_error", message: String(e.message), stack: String(e.error?.stack || "") });
});
window.addEventListener("unhandledrejection", (e) => {
  void api.feedback({ kind: "unhandled_rejection", message: String(e.reason) });
});

const root = createRoot(document.getElementById("root")!);
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
