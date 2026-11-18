import { AgentSelect } from "./agent-select.ts";

if (!customElements.get("mirally-agent-select")) {
  customElements.define("mirally-agent-select", AgentSelect);
}

declare global {
  interface HTMLElementTagNameMap {
    "mirally-agent-select": AgentSelect;
  }
}
