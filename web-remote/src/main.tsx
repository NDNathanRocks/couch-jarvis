import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";
import { forwardToNewAddress, importFromOldAddress } from "./utils/handoff";

const root = document.getElementById("root");
if (!root) throw new Error("#root missing from index.html");

// The old address forwards to the new one (see utils/handoff.ts); the new one takes it in.
if (!forwardToNewAddress(import.meta.env.VITE_HANDOFF_URL)) {
  importFromOldAddress();
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
