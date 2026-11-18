import { ApprovalPage } from "./approval-page.ts";

if (!customElements.get("mirally-approval-page")) {
  customElements.define("mirally-approval-page", ApprovalPage);
}
