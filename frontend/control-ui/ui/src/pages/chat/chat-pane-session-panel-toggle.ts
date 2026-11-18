import type { ControlUiLinkReaderDescriptor } from "../../../../src/shared/control-ui-link-reader.js";
import { resolveLinkReaderTarget } from "../../components/link-reader-target.ts";
import {
  BROWSER_PANEL_TOGGLE_EVENT,
  LINK_READER_PANEL_TOGGLE_EVENT,
  DESKTOP_PANEL_TOGGLE_EVENT,
  PORTAL_PANEL_TOGGLE_EVENT,
  TERMINAL_PANEL_DOCK_BOTTOM_EVENT,
  TERMINAL_PANEL_TOGGLE_EVENT,
} from "../../components/panel-toggle-contract.ts";
import {
  clearSessionPanelToggle,
  panelToggleSessionKey,
  takeSessionPanelToggle,
  type SessionPanelToggleSlot,
} from "../../components/session-panel-toggle-buffer.ts";
import {
  terminalIntentQueue,
  terminalToggleIntent,
} from "../../components/terminal/terminal-pending-actions.ts";
import { areUiSessionKeysEquivalent } from "../../lib/sessions/session-key.ts";
import type { ChatPageHost } from "./chat-state-host.ts";
import { resolveChatAgentId } from "./chat-state-route.ts";
import { closeSlot, openSlot, setSidebarDock } from "./sidebar-layout.ts";

type PanelTagName =
  | "mirally-link-reader-panel"
  | "mirally-browser-panel"
  | "mirally-desktop-panel"
  | "mirally-portals-page"
  | "mirally-terminal-panel";

interface ActivePanelOwner {
  renderRoot: ParentNode;
  state: ChatPageHost;
  linkReaders: readonly ControlUiLinkReaderDescriptor[];
  updateComplete: Promise<unknown>;
}

export type PendingSessionPanelToggle = {
  events: Event[];
  owner: ChatPageHost;
  sessionKey: string;
};

interface SessionPanelToggleControllerOptions {
  current: () => ActivePanelOwner | null;
  pending: Map<SessionPanelToggleSlot, PendingSessionPanelToggle>;
  requestUpdate: () => void;
  updateSidebarLayout: (layout: ChatPageHost["sidebarLayout"]) => void;
}

const panelToggleEvents = [
  [TERMINAL_PANEL_TOGGLE_EVENT, "terminal", "mirally-terminal-panel"],
  [BROWSER_PANEL_TOGGLE_EVENT, "browser", "mirally-browser-panel"],
  [LINK_READER_PANEL_TOGGLE_EVENT, "link-reader", "mirally-link-reader-panel"],
  [DESKTOP_PANEL_TOGGLE_EVENT, "desktop", "mirally-desktop-panel"],
  [PORTAL_PANEL_TOGGLE_EVENT, "portal", "mirally-portals-page"],
] as const;

/** Owns shell-to-pane panel intent handoff for the active chat presentation. */
export class ChatPaneSessionPanelToggleController {
  constructor(private readonly options: SessionPanelToggleControllerOptions) {}

  subscribe(): () => void {
    const cleanups = panelToggleEvents.map(([eventName, slot, tagName]) => {
      const listener = (event: Event) => {
        this.handle(slot, tagName, event);
      };
      window.addEventListener(eventName, listener);
      return () => window.removeEventListener(eventName, listener);
    });
    const handleTerminalDockBottom = () => {
      const owner = this.options.current();
      if (owner) {
        owner.state.updateSidebarLayout(closeSlot(owner.state.sidebarLayout, "terminal"));
      }
    };
    window.addEventListener(TERMINAL_PANEL_DOCK_BOTTOM_EVENT, handleTerminalDockBottom);
    return () => {
      cleanups.forEach((cleanup) => cleanup());
      window.removeEventListener(TERMINAL_PANEL_DOCK_BOTTOM_EVENT, handleTerminalDockBottom);
      this.options.pending.clear();
    };
  }

  handle(slot: SessionPanelToggleSlot, tagName: PanelTagName, event: Event): boolean {
    const owner = this.options.current();
    if (!owner) {
      return false;
    }
    const requestedSession = panelToggleSessionKey(event);
    if (requestedSession && !areUiSessionKeysEquivalent(requestedSession, owner.state.sessionKey)) {
      return false;
    }
    const detail = event instanceof CustomEvent ? event.detail : null;
    if (
      slot === "link-reader" &&
      detail?.open !== false &&
      (!owner.state.connected ||
        !owner.state.client ||
        owner.linkReaders.length === 0 ||
        (detail?.url !== undefined && !resolveLinkReaderTarget(detail.url, owner.linkReaders)))
    ) {
      clearSessionPanelToggle(slot, event);
      return false;
    }
    clearSessionPanelToggle(slot, event);
    if (slot === "link-reader") {
      event.preventDefault();
    }
    if (detail?.open === false) {
      this.options.pending.delete(slot);
      this.options.updateSidebarLayout(closeSlot(owner.state.sidebarLayout, slot));
      return true;
    }
    let layout = openSlot(owner.state.sidebarLayout, slot);
    if (detail?.dock === "right" || detail?.dock === "bottom") {
      layout = setSidebarDock(layout, detail.dock);
    }
    const panel = layout.columns
      .flatMap((column) => column.panels)
      .find((entry) => entry.slot === slot);
    if (
      panel &&
      slot === "desktop" &&
      requestedSession &&
      typeof detail?.environmentId === "string"
    ) {
      panel.environmentId = detail.environmentId;
    }
    if (panel && slot === "portal" && typeof detail?.portalId === "string") {
      panel.portalId = detail.portalId;
      delete panel.environmentId;
    } else if (panel && slot === "portal" && typeof detail?.environmentId === "string") {
      panel.environmentId = detail.environmentId;
      delete panel.portalId;
    }
    if (slot === "terminal") {
      const intent = terminalToggleIntent(event, resolveChatAgentId(owner.state));
      const embeddedTerminal = owner.renderRoot.querySelector("mirally-terminal-panel[embedded]");
      const terminalConstructor = customElements.get("mirally-terminal-panel");
      const embeddedTerminalMounted =
        embeddedTerminal !== null &&
        terminalConstructor !== undefined &&
        embeddedTerminal instanceof terminalConstructor;
      if (intent) {
        void terminalIntentQueue.queue(intent, {
          deferUntilHostChange: !embeddedTerminalMounted,
        });
      }
      this.options.updateSidebarLayout(layout);
      return true;
    }
    const existing = this.options.pending.get(slot);
    if (
      slot === "link-reader" &&
      existing?.owner === owner.state &&
      existing.sessionKey === owner.state.sessionKey
    ) {
      existing.events.push(event);
      this.options.updateSidebarLayout(layout);
      return true;
    }
    const queue: PendingSessionPanelToggle = {
      events: [event],
      owner: owner.state,
      sessionKey: owner.state.sessionKey,
    };
    const sessionKey = owner.state.sessionKey;
    const isCurrent = () =>
      this.options.pending.get(slot) === queue &&
      this.options.current()?.state === owner.state &&
      owner.state.sessionKey === sessionKey;
    this.options.pending.set(slot, queue);
    this.options.updateSidebarLayout(layout);
    void Promise.all([
      customElements.whenDefined("mirally-chat-sidebar-region"),
      customElements.whenDefined(tagName),
    ])
      .then(async () => {
        this.options.requestUpdate();
        await owner.updateComplete;
        if (!isCurrent()) {
          return;
        }
        const region = owner.renderRoot.querySelector<
          HTMLElementTagNameMap["mirally-chat-sidebar-region"]
        >("mirally-chat-sidebar-region");
        await region?.updateComplete;
        if (!isCurrent()) {
          return;
        }
        for (const pendingEvent of queue.events) {
          if (!isCurrent()) {
            return;
          }
          const current = this.options.current();
          const pendingDetail = pendingEvent instanceof CustomEvent ? pendingEvent.detail : null;
          if (
            slot === "link-reader" &&
            (!current?.state.connected ||
              !current.state.client ||
              current.linkReaders.length === 0 ||
              (pendingDetail?.url !== undefined &&
                !resolveLinkReaderTarget(pendingDetail.url, current.linkReaders)))
          ) {
            continue;
          }
          region?.deliverPanelEvent(slot, pendingEvent);
        }
      })
      .finally(() => {
        if (this.options.pending.get(slot) === queue) {
          this.options.pending.delete(slot);
          this.options.requestUpdate();
        }
      });
    return true;
  }

  flush(): void {
    const owner = this.options.current();
    if (!owner) {
      return;
    }
    for (const [, slot, tagName] of panelToggleEvents) {
      let event: Event | null;
      while ((event = takeSessionPanelToggle(slot, owner.state.sessionKey))) {
        this.handle(slot, tagName, event);
      }
    }
  }
}
