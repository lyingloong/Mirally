// Mirally's local product menu keeps the retained sidebar layout and focus behavior.
import { html, nothing } from "lit";
import { mirallyNavigationText, titleForRoute, type NavigationRouteId } from "../app-navigation.ts";
import { pathForRoute } from "../app-route-paths.ts";
import type { ApplicationNavigationOptions } from "../app/context.ts";
import type { ThemeMode } from "../app/theme.ts";
import { t } from "../i18n/index.ts";
import { formatKeyboardShortcutCombo, KEYBOARD_SHORTCUT_COMBOS } from "../lib/keyboard-shortcut-catalog.ts";
import { shouldHandleNavigationClick } from "../lib/navigation-click.ts";
import { openExternalUrlSafe } from "../lib/open-external-url.ts";
import type { PresenceViewer } from "../lib/presence-users.ts";
import {
  closeMenuAfterOwnDropdownHide,
  COMMAND_VALUE_PREFIX,
  LINK_VALUE_PREFIX,
  moveSidebarMenuFocus,
  renderSidebarHelpMenu,
} from "./app-sidebar-agent-menu.ts";
import { icons } from "./icons.ts";
import { trackDropdownKeyboardDismissal } from "./web-awesome.ts";
import "./viewer-facepile.ts";

type SidebarIdentityMenuParams = {
  position: { x: number; bottom: number; width: number };
  canPairDevice: boolean;
  basePath: string;
  gatewayVersion: string | null;
  updateAttentionDismissed: boolean;
  profileViewer?: PresenceViewer;
  canRetryConnection: boolean;
  themeMode: ThemeMode;
  triggerWidth: number;
  onTabAway: () => void;
  onClose: (restoreFocus?: boolean) => void;
  onNavigate: (routeId: NavigationRouteId, options?: ApplicationNavigationOptions) => void;
  onPairMobile: () => void;
  onRetryConnect?: () => void;
};

export function renderSidebarIdentityMenu(params: SidebarIdentityMenuParams) {
  const position = params.position;
  const menuLabel = mirallyNavigationText("个人菜单", "Personal menu");
  const profileName = params.profileViewer?.name ?? params.profileViewer?.email ?? mirallyNavigationText("我的 Mirally", "My Mirally");
  const avatarUser = { id: "owner", watchedSessions: [], ...params.profileViewer, name: profileName };
  const profileEmail = params.profileViewer?.email && params.profileViewer.email !== profileName
    ? params.profileViewer.email : null;
  return html`
    <wa-dropdown
      class="sidebar-customize-menu sidebar-identity-menu"
      style=${`--sidebar-identity-menu-min-width: ${params.triggerWidth}px`}
      .open=${true}
      placement="top-start"
      .distance=${0}
      aria-label=${menuLabel}
      @wa-select=${(event: CustomEvent<{ item: HTMLElement & { value?: string } }>) => {
        event.preventDefault();
        const item = event.detail.item;
        if (item.dataset.nativeNavigation) {
          delete item.dataset.nativeNavigation;
          params.onClose(false);
          return;
        }
        const value = item.value;
        if (!value) return;
        params.onClose(false);
        if (value.startsWith(LINK_VALUE_PREFIX)) {
          openExternalUrlSafe(decodeURIComponent(value.slice(LINK_VALUE_PREFIX.length)));
          return;
        }
        switch (value) {
          case `${COMMAND_VALUE_PREFIX}profile`: params.onNavigate("profile"); break;
          case `${COMMAND_VALUE_PREFIX}settings`: params.onNavigate("config"); break;
          case `${COMMAND_VALUE_PREFIX}appearance`: params.onNavigate("appearance"); break;
          case `${COMMAND_VALUE_PREFIX}new-session`: params.onNavigate("new-session"); break;
          case `${COMMAND_VALUE_PREFIX}about`: params.onNavigate("about"); break;
          case `${COMMAND_VALUE_PREFIX}retry-connect`: params.onRetryConnect?.(); break;
        }
      }}
      @keydown=${(event: KeyboardEvent) => {
        if (!moveSidebarMenuFocus(event)) trackDropdownKeyboardDismissal(event, params.onTabAway);
      }}
      @wa-after-hide=${(event: Event) => closeMenuAfterOwnDropdownHide(event, params.onClose)}
    >
      <button slot="trigger" type="button" tabindex="-1" aria-hidden="true" aria-label=${menuLabel}
        style="position: fixed; left: ${position.x}px; bottom: ${position.bottom}px; width: 1px; height: 1px; opacity: 0; pointer-events: none;"
      ></button>
      <wa-dropdown-item class="sidebar-customize-menu__item sidebar-identity-menu__header" value="command:profile">
        <span slot="icon" class="sidebar-identity-menu__avatar" aria-hidden="true">
          <openclaw-viewer-avatar .user=${avatarUser} variant="footer"></openclaw-viewer-avatar>
        </span>
        <span class="sidebar-identity-menu__identity">
          <span class="sidebar-identity-menu__name" title=${profileName}>${profileName}</span>
          ${profileEmail ? html`<span class="sidebar-identity-menu__email" title=${profileEmail}>${profileEmail}</span>` : nothing}
        </span>
      </wa-dropdown-item>
      <div class="sidebar-customize-menu__separator" role="separator"></div>
      <wa-dropdown-item class="sidebar-customize-menu__item" value="command:new-session">
        <span slot="icon" class="nav-item__icon" aria-hidden="true">${icons.plus}</span>
        <span class="sidebar-customize-menu__text">${titleForRoute("new-session")}</span>
      </wa-dropdown-item>
      <wa-dropdown-item class="sidebar-customize-menu__item" value="command:settings">
        <span slot="icon" class="nav-item__icon" aria-hidden="true">${icons.settings}</span>
        <span class="sidebar-customize-menu__text">${titleForRoute("settings")}</span>
      </wa-dropdown-item>
      <wa-dropdown-item class="sidebar-customize-menu__item" value="command:appearance">
        <span slot="icon" class="nav-item__icon" aria-hidden="true">${icons.palette}</span>
        <span class="sidebar-customize-menu__text">${titleForRoute("appearance")}</span>
        <kbd slot="details" class="session-menu__shortcut" aria-hidden="true">${formatKeyboardShortcutCombo(KEYBOARD_SHORTCUT_COMBOS.appearanceSettings)}</kbd>
      </wa-dropdown-item>
      <wa-dropdown-item class="sidebar-customize-menu__item" value="command:about">
        <span slot="icon" class="nav-item__icon" aria-hidden="true">${icons.circleQuestionMark}</span>
        <span class="sidebar-customize-menu__text">${titleForRoute("about")}</span>
      </wa-dropdown-item>
      <div class="sidebar-customize-menu__separator" role="separator"></div>
      ${renderSidebarHelpMenu()}
      ${params.canRetryConnection ? html`
        <div class="sidebar-customize-menu__separator" role="separator"></div>
        <wa-dropdown-item class="sidebar-customize-menu__item sidebar-identity-menu__retry" value="command:retry-connect">
          <span class="sidebar-customize-menu__text">${t("connection.retryNow")}</span>
        </wa-dropdown-item>` : nothing}
      <div class="sidebar-customize-menu__separator" role="separator"></div>
      <div class="sidebar-identity-menu__footer">
        <a class="sidebar-footer-build" href=${pathForRoute("about", params.basePath)}
          aria-label=${titleForRoute("about")}
          @click=${(event: MouseEvent) => {
            if (!shouldHandleNavigationClick(event)) return;
            event.preventDefault();
            params.onClose();
            params.onNavigate("about");
          }}
        ><span class="sidebar-footer-build__text">Mirally</span></a>
        <span class="sidebar-mode-switch"><openclaw-theme-mode-toggle .mode=${params.themeMode}></openclaw-theme-mode-toggle></span>
      </div>
    </wa-dropdown>
  `;
}
