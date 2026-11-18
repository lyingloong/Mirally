import { MirallyTerminalPanel } from "./terminal-panel.ts";

// Guarded define so shared registries can retain this module across reloads.
if (!customElements.get("mirally-terminal-panel")) {
  customElements.define("mirally-terminal-panel", MirallyTerminalPanel);
}
