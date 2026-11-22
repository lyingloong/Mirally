import { html } from "lit";
import { AgentRosterElement } from "../../lib/agents/roster-element.ts";
import { canCallGatewayMethod } from "../../lib/gateway-methods.ts";
import { renderAgentsHome } from "./view.ts";

export class AgentsHomePage extends AgentRosterElement {
  override render() {
    return this.avatars.withActiveRoutes(() => {
      return renderAgentsHome({
        cards: this.cards().toSorted(
          (a, b) =>
            Number(b.activeNow) - Number(a.activeNow) ||
            b.lastActiveAt - a.lastActiveAt ||
            Number(b.id === this.context.agents.state.agentsList?.defaultId) -
              Number(a.id === this.context.agents.state.agentsList?.defaultId) ||
            a.id.localeCompare(b.id),
        ),
        context: this.context,
        connected: this.connected,
        loading: this.roster.loading,
        error: this.roster.error ?? this.roster.subscriptionError,
        onRetry: () => void this.refresh(),
        canCreate: canCallGatewayMethod(
          this.context.gateway.snapshot,
          "mirally.chat",
          "operator.admin",
        ),
      });
    });
  }
}

export const header = true;
export const render = () => html`<mirally-agents-home-page></mirally-agents-home-page>`;

if (!customElements.get("mirally-agents-home-page")) {
  customElements.define("mirally-agents-home-page", AgentsHomePage);
}
