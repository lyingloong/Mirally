// Public custom-element entrypoint for the Control UI chat pane.
import { ChatPane } from "./chat-pane-render.ts";

if (!customElements.get("mirally-chat-pane")) {
  customElements.define("mirally-chat-pane", ChatPane);
}

declare global {
  interface HTMLElementTagNameMap {
    "mirally-chat-pane": ChatPane;
  }
}
