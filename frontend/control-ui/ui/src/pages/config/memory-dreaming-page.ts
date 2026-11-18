// Dreams tab host. Agent selection is owned by the parent Memory page.
import { html, nothing } from "lit";
import { property } from "lit/decorators.js";
import { MirallyLightDomElement } from "../../lit/mirally-element.ts";
import "../agents/memory/memory-panel.ts";

class MemoryDreamingSettings extends MirallyLightDomElement {
  @property() agentId: string | null = null;

  override render() {
    return html`
      ${
        this.agentId
          ? html`<mirally-agent-memory-panel
              .agentId=${this.agentId}
            ></mirally-agent-memory-panel>`
          : nothing
      }
    `;
  }
}

if (!customElements.get("mirally-memory-dreaming")) {
  customElements.define("mirally-memory-dreaming", MemoryDreamingSettings);
}
