import { html } from "lit";
import type { CustodianRouteData } from "./route.ts";

export function renderCustodianRoute(data: CustodianRouteData | undefined) {
  return html`
    <mirally-custodian-page
      .onboarding=${data?.onboarding === true}
      .newAgentIntent=${data?.intent === "new-agent"}
    ></mirally-custodian-page>
  `;
}
