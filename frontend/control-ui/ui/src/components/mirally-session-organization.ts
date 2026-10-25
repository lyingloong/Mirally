import {html, nothing} from 'lit';
import {t} from '../i18n/index.ts';
import {icons} from './icons.ts';
import type {SessionMenuData, SessionManagementActionKind} from './session-menu-actions.ts';

/** Shared native conversation actions in the retained sidebar and header menus. */
export function renderMirallySessionOrganization(session: SessionMenuData,
  reasons: Partial<Record<SessionManagementActionKind, string>>, archiveAllowed: boolean, disabled = false) {
  if (!session.sessionId) return nothing;
  const items = [
    {kind: 'rename', label: t('sessionsView.renameSessionMenu'), icon: icons.pencil, unavailable: false},
    {kind: 'toggle-pin', label: t(session.pinned ? 'sessionsView.unpinSession' : 'sessionsView.pinSession'),
      icon: icons.pin, unavailable: session.archived || session.isChild || session.pinnable === false},
    {kind: 'toggle-archived', label: t(session.archived ? 'sessionsView.restoreSession' : 'sessionsView.archiveSession'),
      icon: icons.archive, unavailable: session.archiving || !session.archived && !archiveAllowed},
  ] as const;
  return html`${items.map(item => html`<wa-dropdown-item class="session-menu__item" value=${item.kind}
    ?disabled=${disabled || item.unavailable || !!reasons[item.kind]} title=${reasons[item.kind] ?? nothing}>
    <span slot="icon" class="session-menu__icon" aria-hidden="true">${item.icon}</span>
    <span class="session-menu__text">${item.label}</span>
  </wa-dropdown-item>`)}<div class="session-menu__separator" role="separator"></div>`;
}
