import { html, nothing } from "lit";
import { property } from "lit/decorators.js";
import { keyed } from "lit/directives/keyed.js";
import { t } from "../i18n/index.ts";
import { mirallyNavigationText } from "../app-navigation.ts";
import { OpenClawLightDomElement } from "../lit/openclaw-element.ts";
import { DropdownMenuController } from "./dropdown-menu-controller.ts";
import {renderMirallySessionOrganization} from './mirally-session-organization.ts';
import { icons } from "./icons.ts";
import { activateMenuShortcut, menuShortcutHint } from "./menu-shortcuts.ts";
import { promoteToPopoverTopLayer } from "./menu-surface.ts";
import {
  EMPTY_SESSION_MENU_DATA,
  type SessionManagementAction,
  type SessionMenuData,
} from "./session-menu-actions.ts";
import type { SessionCreatedActor } from "./session-owner-chip.ts";

/**
 * Worktree-session extras resolved lazily by the menu host after open.
 * Only resolved destinations are offered. A null `worktreePath` hides editor
 * destinations — see
 * `native-editor-locality.runtime.ts` for which checkouts ever get one.
 */
export type SessionMenuWork = {
  loading: boolean;
  pullRequestUrl: string | null;
  worktreePath: string | null;
};

export type SessionMenuAction =
  | SessionManagementAction
  | { kind: "open-pr"; url: string }
  | { kind: "plugin"; id: string }
  | { kind: "stop-cloud-worker" };

export type SessionMenuActionKind = SessionMenuAction["kind"];

export type PluginSessionMenuAction = { id: string; label: string; disabled?: boolean };

class SessionMenu extends OpenClawLightDomElement {
  @property({ attribute: false }) session: SessionMenuData = EMPTY_SESSION_MENU_DATA;
  @property({ attribute: false }) compact = false;
  @property({ attribute: false }) navigationAllowed = false;
  @property({ attribute: false }) copyMarkdownAllowed = false;
  @property({ attribute: false }) splitAllowed = false;
  // Batch selections retain menu focus and dismissal, but have no product
  // lifecycle action until Mirally exposes a supported batch contract.
  @property({ attribute: false }) selectionCount = 1;
  @property({ attribute: false }) lastActive = "";
  @property({ attribute: false }) anchor: { x: number; y: number } = { x: 0, y: 0 };
  @property({ attribute: false }) trigger: HTMLElement | null = null;
  @property({ attribute: false }) disabled = false;
  @property({ attribute: false }) actionDisabledReasons: Partial<
    Record<SessionMenuActionKind, string>
  > = {};
  @property({ attribute: false }) forkDisabled = false;
  @property({ attribute: false }) forkFromLastCompleted = false;
  @property({ attribute: false }) archiveAllowed = false;
  @property({ attribute: false }) deleteAllowed = false;
  @property({ attribute: false }) cloudWorkerStopAllowed = false;
  @property({ attribute: false }) groups: readonly string[] = [];
  @property({ attribute: false }) currentOwner: SessionCreatedActor | null = null;
  @property({ attribute: false }) work: SessionMenuWork | null = null;
  @property({ attribute: false }) pluginActions: readonly PluginSessionMenuAction[] = [];
  @property({ attribute: false }) onAction: (action: SessionMenuAction) => void = () => {};
  @property({ attribute: false }) onClose: () => void = () => {};
  readonly menuLifecycle = new DropdownMenuController(this, {
    getTrigger: () => this.trigger,
    onClose: () => this.onClose(),
    onKeydown: (event) => { activateMenuShortcut(this, event); },
  });

  override connectedCallback() {
    super.connectedCallback();
    // Sidebar-hosted menus live inside the nav stacking context (z-index 10),
    // which paints below the sidebar resizer divider (z-index 20); promoting
    // the menu to the popover top layer keeps app chrome from bleeding
    // through it (same pattern as openclaw-native-link-menu).
    promoteToPopoverTopLayer(this);
  }

  private runAction(action: SessionMenuAction) {
    if (this.disabled || this.selectionCount !== 1 || this.actionDisabledReasons[action.kind]) return;
    if (action.kind === 'toggle-pin' && (this.session.archived || this.session.isChild || this.session.pinnable === false)) return;
    if (action.kind === 'toggle-archived' && (this.session.archiving || !this.session.archived && !this.archiveAllowed)) return;
    if (action.kind === "copy-markdown" && !this.copyMarkdownAllowed) return;
    if (["copy-session-link", "open-new-tab"].includes(action.kind) && !this.navigationAllowed) return;
    this.onClose();
    this.onAction(action);
  }

  private actionDisabled(kind: SessionMenuActionKind, extra = false): boolean {
    return this.disabled || extra || Boolean(this.actionDisabledReasons[kind]);
  }

  private actionTitle(kind: SessionMenuActionKind): string | typeof nothing {
    return this.actionDisabledReasons[kind] ?? nothing;
  }

  private readonly handleSelect = (event: CustomEvent<{ item: { value?: string } }>) => {
    event.preventDefault();
    const value = event.detail.item.value;
    if (!value) {
      return;
    }
    // Mirally has one native conversation owner. Unsupported lifecycle actions
    // must not fall through to the old method-level sessions.create contract.
    if (value === "copy-markdown" || value === "copy-session-link" || value === "open-new-tab" || value === 'rename' || value === 'toggle-pin' || value === 'toggle-archived') {
      this.runAction({ kind: value });
    }
  };

  private readonly handleAfterHide = (event: Event) => {
    // A keyed replacement can finish hiding after its successor opens.
    if (event.currentTarget instanceof Node && event.currentTarget.isConnected) {
      this.onClose();
    }
  };

  override render() {
    const menuWidth = 240;
    const menuMaxHeight = 460;
    const clampedX = Math.max(8, Math.min(this.anchor.x, window.innerWidth - menuWidth - 8));
    const clampedY = Math.max(8, Math.min(this.anchor.y, window.innerHeight - menuMaxHeight - 8));
    const session = this.session;
    const batch = this.selectionCount > 1;
    const count = String(this.selectionCount);
    const menuLabel = batch
      ? t("chat.sidebar.sessionMenuMany", { count })
      : t("chat.sidebar.sessionMenu", { session: session.label });
    return keyed(
      this.anchor,
      html`<wa-dropdown
        class=${`session-menu${this.compact ? " session-menu--compact" : ""}`}
        .open=${true}
        placement="bottom-start"
        .distance=${0}
        aria-label=${menuLabel}
        @wa-select=${this.handleSelect}
        @wa-after-hide=${this.handleAfterHide}
      >
        <button
          slot="trigger"
          type="button"
          tabindex="-1"
          aria-hidden="true"
          aria-label=${menuLabel}
          style="position: fixed; left: ${clampedX}px; top: ${clampedY}px; width: 1px; height: 1px; opacity: 0; pointer-events: none;"
        ></button>
        ${!batch && this.lastActive ? html`
          <div class="session-menu__info">${t("sessionsView.lastActive", { time: this.lastActive })}</div>` : nothing}
        ${batch ? html`
          <div class="session-menu__info">${mirallyNavigationText("选择一个对话以复制或打开。", "Select one conversation to copy or open it.")}</div>
        ` : html`
          ${renderMirallySessionOrganization(session, this.actionDisabledReasons, this.archiveAllowed, this.disabled)}
          <wa-dropdown-item
            class="session-menu__item"
            value="copy-markdown"
            data-shortcut="c"
            aria-keyshortcuts="C"
            ?disabled=${this.actionDisabled("copy-markdown", !this.copyMarkdownAllowed)}
            title=${this.actionTitle("copy-markdown")}
          >
            <span slot="icon" class="session-menu__icon" aria-hidden="true">${icons.fileText}</span>
            <span class="session-menu__text">${t("sessionsView.copyMarkdown")}</span>
            ${menuShortcutHint("c")}
          </wa-dropdown-item>
          <wa-dropdown-item
            class="session-menu__item"
            value="copy-session-link"
            ?disabled=${this.actionDisabled("copy-session-link", !this.navigationAllowed)}
            title=${this.actionTitle("copy-session-link")}
          >
            <span slot="icon" class="session-menu__icon" aria-hidden="true">${icons.link}</span>
            <span class="session-menu__text">${t("sessionsView.copySessionLink")}</span>
          </wa-dropdown-item>
          <div class="session-menu__separator" role="separator"></div>
          <wa-dropdown-item
            class="session-menu__item"
            value="open-new-tab"
            data-new-tab-action
            ?disabled=${this.actionDisabled("open-new-tab", !this.navigationAllowed)}
            title=${this.actionTitle("open-new-tab")}
          >
            <span slot="icon" class="session-menu__icon" aria-hidden="true">${icons.externalLink}</span>
            <span class="session-menu__text">${t("sessionsView.openNewTab")}</span>
          </wa-dropdown-item>
        `}

      </wa-dropdown>`,
    );
  }
}

if (!customElements.get("openclaw-session-menu")) {
  customElements.define("openclaw-session-menu", SessionMenu);
}
