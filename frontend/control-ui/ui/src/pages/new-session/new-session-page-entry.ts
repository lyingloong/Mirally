import { html } from "lit";
import { restoredInstantThreadPage } from "./instant-thread-restore.ts";
import { NewSessionPage } from "./new-session-page.ts";

if (!customElements.get("mirally-new-session-page")) {
  customElements.define("mirally-new-session-page", NewSessionPage);
}

export const render = (data: unknown) =>
  restoredInstantThreadPage(data) ??
  html`<mirally-new-session-page .data=${data}></mirally-new-session-page>`;
