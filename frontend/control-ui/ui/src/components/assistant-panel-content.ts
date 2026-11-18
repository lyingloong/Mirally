import { html, nothing } from "lit";
import { property } from "lit/decorators.js";
import type { RouteId } from "../app-route-paths.ts";
import type { ApplicationContext } from "../app/context.ts";
import { MirallyLightDomElement } from "../lit/mirally-element.ts";
import { SubscriptionsController } from "../lit/subscriptions-controller.ts";
import { buildHomeWorkContext, subscribeChatWorkContext } from "../pages/chat/chat-work-context.ts";
import {
  custodianSessionStore,
  type CustodianSessionStore,
} from "../pages/custodian/custodian-session-store.ts";
import "../pages/custodian/custodian-surface.ts";
import "./home-session.runtime.ts";
import "../styles/assistant-panel-content.css";

/** Conversation runtimes load inside the already-open dock. */
export class MirallyAssistantPanelContent extends MirallyLightDomElement {
  @property({ type: Boolean }) active = false;
  @property() destination: "home" | "custodian" = "custodian";
  @property() sessionKey = "";
  @property() agentId = "";
  @property({ attribute: false }) context: ApplicationContext | undefined;
  @property() pageRouteId: RouteId = "chat";
  @property() pageSessionKey = "";
  @property() pageAgentId = "";
  @property({ attribute: false }) store: CustodianSessionStore | undefined;

  private custodianVisible = false;

  constructor() {
    super();
    void new SubscriptionsController(this)
      .watch(
        () => this.store ?? custodianSessionStore,
        (store, notify) => store.subscribe(notify),
      )
      .watch(
        () => this.context,
        (context, notify) => subscribeChatWorkContext(context, notify),
      )
      .watch(
        () => this.context?.sessions,
        (sessions, notify) => sessions.subscribe(notify),
      )
      .watch(
        () => this.context?.agents,
        (agents, notify) => agents.subscribe(notify),
      )
      .watch(
        () => this.context?.gateway,
        (gateway, notify) => gateway.subscribe(notify),
      );
  }

  override connectedCallback(): void {
    super.connectedCallback();
    // The lightweight frame uses the existing store for minimize and mascot
    // state, without importing its conversation runtime during application boot.
    this.dispatchEvent(
      new CustomEvent("assistant-custodian-store", {
        detail: this.store ?? custodianSessionStore,
        bubbles: true,
      }),
    );
  }

  override willUpdate(): void {
    const visible = this.active && this.destination === "custodian";
    if (visible && !this.custodianVisible) {
      void (this.store ?? custodianSessionStore).refreshTranscriptIfIdle();
    }
    this.custodianVisible = visible;
  }

  override render() {
    if (!this.active) {
      return nothing;
    }
    const store = this.store ?? custodianSessionStore;
    return this.destination === "home"
      ? html`<mirally-home-session
          .sessionKey=${this.sessionKey}
          .agentId=${this.agentId}
          .workContext=${
            this.context
              ? buildHomeWorkContext(
                  this.context,
                  this.pageRouteId,
                  this.pageSessionKey,
                  this.pageAgentId,
                )
              : undefined
          }
        ></mirally-home-session>`
      : html`<mirally-custodian-surface
          .store=${store}
          .onboarding=${store.activeVariant === "onboarding"}
          .newAgentIntent=${store.activeVariant === "new-agent"}
          compact
        ></mirally-custodian-surface>`;
  }
}

customElements.define("mirally-assistant-panel-content", MirallyAssistantPanelContent);

declare global {
  interface HTMLElementTagNameMap {
    "mirally-assistant-panel-content": MirallyAssistantPanelContent;
  }
}
