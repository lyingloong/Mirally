import { html, nothing, type TemplateResult } from "lit";
import {
  BUILTIN_THEMES,
  resolveThemeBranding,
} from "../../../../packages/gateway-protocol/src/theme.ts";
import type { ServerUiPrefProvenance } from "../../app/server-prefs.ts";
import {
  normalizeCatalogOpenTarget,
  normalizeChatFollowUpMode,
  normalizeChatSendShortcut,
  UI_APPEARANCE_DEFAULTS,
} from "../../app/settings.ts";
import "../../components/tooltip.ts";
import {
  renderSettingsDefaultDescription,
  renderSettingsRow,
  renderSettingsToggleRow,
} from "../../components/settings-ui.ts";
import { t } from "../../i18n/index.ts";
import { shouldHandleNavigationClick } from "../../lib/navigation-click.ts";
import { languageLabel, renderLanguageSelect } from "./language-select.ts";
import { APPEARANCE_SETTINGS_TARGET_IDS } from "./route-data.ts";
import { renderSessionObserverSettings } from "./session-observer-settings.ts";
import { renderSettingsSelectRow } from "./settings-select-row.ts";
import type { ConfigProps } from "./view-types.ts";

export function serverUiPrefProvenanceHint(provenance: ServerUiPrefProvenance): string {
  if (provenance === "profile") {
    return t("configView.profileSyncedHint");
  }
  if (provenance === "device-local") {
    return t("quickSettings.personal.browserOnly");
  }
  if (provenance === "pending") {
    return t("configView.syncPendingHint");
  }
  return t("configView.syncedHint");
}

export function renderLanguageSection(props: ConfigProps) {
  const defaultDescription = renderSettingsDefaultDescription(
    props.localeResetValue ? languageLabel(props.localeResetValue) : t("common.system"),
    props.localeOverridden,
  );
  const provenance = serverUiPrefProvenanceHint(props.localeProvenance);
  return html`
    <section id=${APPEARANCE_SETTINGS_TARGET_IDS.language} class="settings-section">
      <div class="settings-section__header">
        <h2 class="settings-section__heading">${t("quickSettings.language")}</h2>
      </div>
      <div class="settings-group">
        ${renderSettingsRow({
          title: t("quickSettings.language"),
          description: html`${defaultDescription} ${provenance}`,
          control: renderLanguageSelect(
            props.localeOverride,
            props.systemLocale,
            props.onLocaleChange,
          ),
        })}
      </div>
    </section>
  `;
}

function renderSettingsMediaDeviceField(options: {
  state: ConfigProps["microphone"];
  title: string;
  systemDefaultLabel: string;
  emptyLabel: string;
  fallbackLabel: (number: number) => string;
  dataAttribute: "microphone" | "camera";
  onRefresh: (() => void) | undefined;
  onSelect: ((deviceId: string) => void) | undefined;
}) {
  const state = options.state;
  if (!state || !options.onSelect) {
    return nothing;
  }
  const selectedDeviceId = state.selectedDeviceId.trim();
  const selectedDeviceKnown = state.devices.some((device) => device.deviceId === selectedDeviceId);
  const selectOptions = [
    { label: options.systemDefaultLabel, value: "" },
    ...state.devices.map((device) => ({ label: device.label, value: device.deviceId })),
    // A remembered device that is unplugged right now stays selectable so the
    // choice survives until the user picks something else.
    ...(selectedDeviceId && !selectedDeviceKnown
      ? [{ label: options.fallbackLabel(state.devices.length + 1), value: selectedDeviceId }]
      : []),
  ];
  let accessRequested = false;
  const requestAccess = () => {
    if (accessRequested || !state.permissionRequired) {
      return;
    }
    accessRequested = true;
    options.onRefresh?.();
  };
  const requestAccessFromPointer = (event: PointerEvent) => {
    if (event.button === 0) {
      requestAccess();
    }
  };
  const requestAccessFromKeyboard = (event: KeyboardEvent) => {
    if (["Enter", " ", "ArrowDown", "ArrowUp", "F4"].includes(event.key)) {
      requestAccess();
    }
  };
  const note = state.error
    ? html`<span role="alert">${state.error}</span>`
    : !state.loading && state.devices.length === 0
      ? options.emptyLabel
      : undefined;
  return renderSettingsRow({
    title: options.title,
    description: html`${note ? html`${note}<br />` : nothing}${t(
      "quickSettings.personal.browserOnly",
    )}`,
    control: html`
      <select
        class="settings-select settings-select--media-device"
        data-settings-microphone=${options.dataAttribute === "microphone" ? "" : nothing}
        data-settings-camera=${options.dataAttribute === "camera" ? "" : nothing}
        aria-label=${options.title}
        .value=${selectedDeviceId}
        @pointerdown=${requestAccessFromPointer}
        @keydown=${requestAccessFromKeyboard}
        @change=${(event: Event) =>
          options.onSelect?.((event.currentTarget as HTMLSelectElement).value)}
      >
        ${selectOptions.map(
          (option) => html`
            <option value=${option.value} ?selected=${option.value === selectedDeviceId}>
              ${option.label}
            </option>
          `,
        )}
      </select>
    `,
  });
}

function renderSettingsMicrophoneField(props: ConfigProps) {
  return renderSettingsMediaDeviceField({
    state: props.microphone,
    title: t("chat.composer.microphoneInput"),
    systemDefaultLabel: t("chat.composer.systemDefaultMicrophone"),
    emptyLabel: t("chat.composer.noMicrophones"),
    fallbackLabel: (number) => t("chat.composer.microphoneFallback", { number: String(number) }),
    dataAttribute: "microphone",
    onRefresh: props.onMicrophoneRefresh,
    onSelect: props.onMicrophoneSelect,
  });
}

function renderSettingsCameraField(props: ConfigProps) {
  return renderSettingsMediaDeviceField({
    state: props.camera,
    title: t("chat.composer.cameraInput"),
    systemDefaultLabel: t("chat.composer.systemDefaultCamera"),
    emptyLabel: t("chat.composer.noCameras"),
    fallbackLabel: (number) => t("chat.composer.cameraFallback", { number: String(number) }),
    dataAttribute: "camera",
    onRefresh: props.onCameraRefresh,
    onSelect: props.onCameraSelect,
  });
}

export function renderChatPreferencesSection(
  props: ConfigProps,
  messageWidthInput: TemplateResult,
) {
  const followUpSelection = props.chatFollowUpMode ?? "server";
  const serverQueueMode = props.serverQueueMode ?? t("chat.followUpModeLoading");
  const followUpDescription = props.chatFollowUpMode
    ? t("chat.followUpModeOverriding", { mode: serverQueueMode })
    : nothing;
  const messageWidthDefaultDescription = renderSettingsDefaultDescription(
    UI_APPEARANCE_DEFAULTS.chatMessageMaxWidth,
    props.chatMessageMaxWidth !== undefined,
  );
  const sendShortcutDefaultDescription = renderSettingsDefaultDescription(
    props.chatSendShortcutResetValue === "modifier-enter"
      ? t("chat.sendShortcutModifierEnter")
      : t("chat.sendShortcutEnter"),
    props.chatSendShortcutOverridden,
  );
  const sendShortcutProvenance = serverUiPrefProvenanceHint(props.chatSendShortcutProvenance);
  const followUpProvenance = serverUiPrefProvenanceHint(props.chatFollowUpModeProvenance);
  const catalogTargetDefaultDescription = renderSettingsDefaultDescription(
    t("chat.catalogOpenTargetViewer"),
    props.catalogOpenTarget !== UI_APPEARANCE_DEFAULTS.catalogOpenTarget,
  );
  const holdToRecordDefaultDescription = renderSettingsDefaultDescription(
    t("common.enabled"),
    (props.composerHoldToRecord ?? UI_APPEARANCE_DEFAULTS.composerHoldToRecord) !==
      UI_APPEARANCE_DEFAULTS.composerHoldToRecord,
  );
  const showTaskProgressDefaultDescription = renderSettingsDefaultDescription(
    t("common.enabled"),
    props.chatShowTaskProgress !== UI_APPEARANCE_DEFAULTS.chatShowTaskProgress,
  );
  const collapseTaskProgressDefaultDescription = renderSettingsDefaultDescription(
    t("common.disabled"),
    props.chatCollapseTaskProgress !== UI_APPEARANCE_DEFAULTS.chatCollapseTaskProgress,
  );
  return html`
    <section id=${APPEARANCE_SETTINGS_TARGET_IDS.chat} class="settings-section">
      <div class="settings-section__header">
        <h2 class="settings-section__heading">${t("configView.chatPrefs.title")}</h2>
      </div>
      <div class="settings-group">
        ${renderSettingsRow({
          title: t("configView.chatPrefs.messageWidth"),
          description: html`${t("configView.chatPrefs.messageWidthHint")}<br />
            ${messageWidthDefaultDescription} ${t("quickSettings.personal.browserOnly")}`,
          control: messageWidthInput,
        })}
        ${renderSettingsToggleRow({
          title: t("configView.chatPrefs.showTaskProgress"),
          description: html`${t("configView.chatPrefs.showTaskProgressHint")}<br />
            ${showTaskProgressDefaultDescription} ${t("quickSettings.personal.browserOnly")}`,
          checked: props.chatShowTaskProgress,
          onChange: props.setChatShowTaskProgress,
        })}
        ${renderSettingsToggleRow({
          title: t("configView.chatPrefs.collapseTaskProgress"),
          description: html`${t("configView.chatPrefs.collapseTaskProgressHint")}<br />
            ${collapseTaskProgressDefaultDescription} ${t("quickSettings.personal.browserOnly")}`,
          checked: props.chatCollapseTaskProgress,
          onChange: props.setChatCollapseTaskProgress,
          disabled: !props.chatShowTaskProgress,
        })}
        ${renderSettingsSelectRow({
          title: t("chat.sendShortcut"),
          value: props.chatSendShortcut,
          setting: "send-shortcut",
          description: html`${sendShortcutDefaultDescription} ${sendShortcutProvenance}`,
          options: [
            { value: "enter", label: t("chat.sendShortcutEnter") },
            { value: "modifier-enter", label: t("chat.sendShortcutModifierEnter") },
          ],
          onChange: (value) => props.setChatSendShortcut(normalizeChatSendShortcut(value)),
        })}
        ${renderSettingsRow({
          title: t("chat.followUpMode"),
          description: html`${followUpDescription} ${followUpProvenance}`,
          control: html`
            <select
              class="settings-select"
              data-settings-follow-up-mode
              aria-label=${t("chat.followUpMode")}
              .value=${followUpSelection}
              @change=${(event: Event) => {
                const value = (event.currentTarget as HTMLSelectElement).value;
                props.setChatFollowUpMode(
                  value === "server" ? undefined : normalizeChatFollowUpMode(value),
                );
              }}
            >
              <option value="server" ?selected=${followUpSelection === "server"}>
                ${t("chat.followUpModeServer", { mode: serverQueueMode })}
              </option>
              <option value="steer" ?selected=${followUpSelection === "steer"}>
                ${t("chat.followUpModeSteer")}
              </option>
              <option value="queue" ?selected=${followUpSelection === "queue"}>
                ${t("chat.followUpModeQueue")}
              </option>
            </select>
            ${
              props.chatFollowUpModeOverridden
                ? html`<button
                    type="button"
                    class="btn btn--sm"
                    @click=${props.resetChatFollowUpMode}
                  >
                    ${t("chat.followUpModeReset")}
                  </button>`
                : nothing
            }
          `,
        })}
        ${renderSettingsSelectRow({
          title: t("chat.catalogOpenTarget"),
          value: props.catalogOpenTarget,
          setting: "catalog-open-target",
          description: html`${catalogTargetDefaultDescription}
          ${t("quickSettings.personal.browserOnly")}`,
          options: [
            { value: "viewer", label: t("chat.catalogOpenTargetViewer") },
            { value: "terminal", label: t("chat.catalogOpenTargetTerminal") },
          ],
          onChange: (value) => props.setCatalogOpenTarget(normalizeCatalogOpenTarget(value)),
        })}
        ${renderSettingsMicrophoneField(props)} ${renderSettingsCameraField(props)}
        ${
          props.setComposerHoldToRecord
            ? renderSettingsToggleRow({
                title: t("chat.composer.holdToRecordSetting"),
                description: html`${t("chat.composer.holdToRecordSettingDescription")}<br />
                  ${holdToRecordDefaultDescription} ${t("quickSettings.personal.browserOnly")}`,
                checked: props.composerHoldToRecord ?? UI_APPEARANCE_DEFAULTS.composerHoldToRecord,
                onChange: props.setComposerHoldToRecord,
              })
            : nothing
        }
      </div>
    </section>
  `;
}

// Keep the existing local visits preference and the original settings layout.
// Native desktop companion forms continue to use Mirally's own companion panel.
export function renderLobsterPetSection(props: ConfigProps) {
  if (!props.setLobsterPetVisits) return nothing;
  return html`<section class="settings-section">
    <div class="settings-section__header"><h2 class="settings-section__heading">Mirally 伙伴</h2></div>
    <div class="settings-group">
      ${renderSettingsToggleRow({
        title: "页面伙伴来访",
        description: "Mirally 的水獭伙伴会偶尔在新对话输入框旁陪伴你。此偏好保存在当前浏览器；桌宠三形态在伙伴菜单中设置。",
        checked: props.lobsterPetVisits ?? UI_APPEARANCE_DEFAULTS.lobsterPetVisits,
        onChange: enabled => props.setLobsterPetVisits?.(enabled),
      })}
    </div>
  </section>`;
}

export function renderSidebarPreferencesSection(props: ConfigProps) {
  const hiddenCatalogIds = [...props.hiddenSessionCatalogIds].toSorted();
  const liveActivityDefaultDescription = renderSettingsDefaultDescription(
    t("common.enabled"),
    props.sidebarLiveActivity !== UI_APPEARANCE_DEFAULTS.sidebarLiveActivity,
  );
  // The delete dialog's "Don't ask me again" writes this off; this row is where
  // the operator turns it back on, so it has to stay next to the session prefs.
  const setSessionDeleteConfirm = props.setSessionDeleteConfirm;
  const sessionDeleteConfirm =
    props.sessionDeleteConfirm ?? UI_APPEARANCE_DEFAULTS.sessionDeleteConfirm;
  const deleteConfirmDefaultDescription = renderSettingsDefaultDescription(
    t("common.enabled"),
    sessionDeleteConfirm !== UI_APPEARANCE_DEFAULTS.sessionDeleteConfirm,
  );
  return html`
    <section id=${APPEARANCE_SETTINGS_TARGET_IDS.sidebar} class="settings-section">
      <div class="settings-section__header">
        <h2 class="settings-section__heading">${t("configView.sidebarPrefs.title")}</h2>
      </div>
      <p class="settings-section__desc">${t("configView.sidebarPrefs.hint")}</p>
      <div class="settings-group">
        ${renderSettingsToggleRow({
          title: t("configView.sidebarPrefs.liveActivity"),
          description: html`${t("configView.sidebarPrefs.liveActivityHint")}<br />
            ${liveActivityDefaultDescription} ${t("quickSettings.personal.browserOnly")}`,
          checked: props.sidebarLiveActivity,
          onChange: props.setSidebarLiveActivity,
        })}
        ${
          setSessionDeleteConfirm
            ? renderSettingsToggleRow({
                title: t("configView.sidebarPrefs.deleteConfirm"),
                description: html`${t("configView.sidebarPrefs.deleteConfirmHint")}<br />
                  ${deleteConfirmDefaultDescription} ${t("quickSettings.personal.browserOnly")}`,
                checked: sessionDeleteConfirm,
                onChange: setSessionDeleteConfirm,
              })
            : nothing
        }
      </div>
      ${
        hiddenCatalogIds.length > 0
          ? html`
              <div class="settings-section__header settings-section__header--subsection">
                <h3 class="settings-section__heading">
                  ${t("chat.sidebar.hiddenSessionSections")}
                </h3>
              </div>
              <div class="settings-group">
                ${hiddenCatalogIds.map((catalogId) =>
                  renderSettingsRow({
                    title: props.hiddenSessionCatalogLabels.get(catalogId) ?? catalogId,
                    description: t("quickSettings.personal.browserOnly"),
                    control: html`<button
                      type="button"
                      class="btn btn--sm"
                      @click=${() => props.setSessionCatalogHidden(catalogId, false)}
                    >
                      ${t("chat.sidebar.showSessionSection")}
                    </button>`,
                  }),
                )}
              </div>
            `
          : nothing
      }
      <div class="settings-section__header settings-section__header--subsection">
        <h3 class="settings-section__heading">${t("configView.sessionObserver.title")}</h3>
      </div>
      <p class="settings-section__desc">${t("configView.sessionObserver.hint")}</p>
      ${renderSessionObserverSettings({
        enabled: props.sessionObserverEnabled !== false,
        utilityModel: props.sessionObserverUtilityModel,
        resolvedUtilityModel: props.sessionObserverResolvedModel,
        models: props.sessionObserverModels ?? [],
        modelsUnavailable: props.sessionObserverModelsUnavailable === true,
        disabled: props.sessionObserverDisabled === true,
        onEnabledChange: (enabled) => props.setSessionObserverEnabled?.(enabled),
        onUtilityModelChange: (selection) => props.setSessionObserverUtilityModel?.(selection),
      })}
    </section>
  `;
}
