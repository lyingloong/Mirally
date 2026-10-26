import { normalizeLowercaseStringOrEmpty } from "@openclaw/normalization-core/string-coerce";
import { isValidWorkboardBoardId } from "@openclaw/workboard-contract";
// Control UI app navigation defines sidebar and settings presentation metadata.
import type { RouteId } from "./app-route-paths.ts";
import type {
  NativeDeviceSettingsCapability,
  NativeDeviceSettingsSnapshot,
} from "./app/native-device-settings.ts";
import type { IconName } from "./components/icons.ts";
import { i18n, t } from "./i18n/index.ts";

export type NavigationRouteId = RouteId;

type NavigationPresentation = readonly [icon: IconName, titleKey: string, subtitleKey: string];

// Keep the original customizable sidebar and session list, with destinations
// owned by Mirally. Legacy route ids stay stable for saved links and preferences.
export const SIDEBAR_NAV_ROUTES = [
  "custodian",
  "cron",
  "activity",
  "memory",
  "plugins",
] as const satisfies readonly NavigationRouteId[];

// Routes presented as tabs of the Plugins hub. The sidebar highlights the
// Plugins entry for all of them, mirroring how config covers settings routes.
const PLUGINS_HUB_ROUTES: ReadonlySet<NavigationRouteId> = new Set([
  "plugins",
  "skills",
  "skill-workshop",
]);

export function isPluginsHubRoute(routeId: NavigationRouteId): boolean {
  return PLUGINS_HUB_ROUTES.has(routeId);
}

// Worktrees renders as a tab of the Sessions hub; the sidebar highlights the
// Sessions entry for both routes, mirroring the Plugins hub behavior.
const SESSIONS_HUB_ROUTES: ReadonlySet<NavigationRouteId> = new Set(["sessions", "worktrees"]);

export function isSessionsHubRoute(routeId: NavigationRouteId): boolean {
  return SESSIONS_HUB_ROUTES.has(routeId);
}

export type SidebarNavRoute = (typeof SIDEBAR_NAV_ROUTES)[number];
export type PersistedSidebarRoute = SidebarNavRoute;

function isPersistedSidebarRoute(value: unknown): value is PersistedSidebarRoute {
  return SIDEBAR_NAV_ROUTES.includes(value as PersistedSidebarRoute);
}

export type SidebarZoneEntry =
  | { type: "route"; route: PersistedSidebarRoute }
  | { type: "plugin"; key: string }
  | { type: "session"; key: string };

export const DEFAULT_SIDEBAR_ENTRIES = SIDEBAR_NAV_ROUTES.map((route) =>
  serializeSidebarEntry({ type: "route", route }),
);

const LEGACY_DEFAULT_SIDEBAR_ENTRIES = [
  "route:agents-home", "route:dashboards", "route:systems", "route:cron", "route:plugins",
];

/**
 * Parse the compact persisted representation used by browser and synced prefs.
 */
export function parseSidebarEntry(value: unknown): SidebarZoneEntry | null {
  if (typeof value !== "string") {
    return null;
  }
  if (value.startsWith("route:")) {
    const route = value.slice("route:".length);
    if (route === "workboard") {
      return { type: "plugin", key: "workboard/workboard" };
    }
    return isPersistedSidebarRoute(route) ? { type: "route", route } : null;
  }
  if (value.startsWith("session:")) {
    const key = value.slice("session:".length).trim();
    return key ? { type: "session", key } : null;
  }
  if (value.startsWith("workboard:")) {
    const boardId = value.slice("workboard:".length).trim();
    // Normalize the shipped Workboard pin format at the preference boundary.
    return isValidWorkboardBoardId(boardId)
      ? { type: "plugin", key: `workboard/board-${boardId}` }
      : null;
  }
  if (value.startsWith("plugin:")) {
    const key = value.slice("plugin:".length);
    // Descriptor ids are opaque, unlike native registration ids. The catalog
    // controls availability; preserving a key never grants access to a plugin.
    const separator = key.indexOf("/");
    return separator > 0 && separator < key.length - 1 ? { type: "plugin", key } : null;
  }
  return null;
}

export function serializeSidebarEntry(entry: SidebarZoneEntry): string {
  if (entry.type === "route") {
    return `route:${entry.route}`;
  }
  return entry.type === "plugin" ? `plugin:${entry.key}` : `session:${entry.key}`;
}

/**
 * Normalize a persisted sidebar-zone list. Returns null when the value is not a
 * list; malformed and duplicate entries are dropped.
 */
export function normalizeSidebarEntries(value: unknown): string[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  if (value.length === LEGACY_DEFAULT_SIDEBAR_ENTRIES.length &&
      value.every((entry, index) => entry === LEGACY_DEFAULT_SIDEBAR_ENTRIES[index])) {
    return [...DEFAULT_SIDEBAR_ENTRIES];
  }
  const normalized: string[] = [];
  for (const valueEntry of value) {
    const parsed = parseSidebarEntry(valueEntry);
    if (!parsed) {
      continue;
    }
    const entry = serializeSidebarEntry(parsed);
    if (!normalized.includes(entry)) {
      normalized.push(entry);
    }
  }
  return normalized;
}

export function sidebarMoreRoutes(entries: readonly string[]): SidebarNavRoute[] {
  const visibleRoutes = new Set(
    entries.flatMap((entry) => {
      const parsed = parseSidebarEntry(entry);
      return parsed?.type === "route" ? [parsed.route] : [];
    }),
  );
  return SIDEBAR_NAV_ROUTES.filter((routeId) => !visibleRoutes.has(routeId));
}

type SettingsNavigationGroup = {
  /** i18n key for the group heading; null renders the group without a label. */
  labelKey: string | null;
  routes: readonly NavigationRouteId[];
};

export type SettingsSearchBlock = {
  routeId: RouteId;
  label: string;
  pathname?: string;
  search?: string;
  hash: string;
};

let settingsSearchSegmenterLocale = "";
let settingsSearchSegmenter: Intl.Segmenter | null = null;

function settingsSearchHasWordPrefix(value: string, query: string): boolean {
  const locale = i18n.getLocale();
  if (settingsSearchSegmenterLocale !== locale) {
    settingsSearchSegmenterLocale = locale;
    settingsSearchSegmenter =
      typeof Intl !== "undefined" && "Segmenter" in Intl
        ? new Intl.Segmenter(locale, { granularity: "word" })
        : null;
  }
  if (!settingsSearchSegmenter) {
    return value.split(/[^\p{L}\p{N}]+/u).some((word) => word.startsWith(query));
  }
  for (const segment of settingsSearchSegmenter.segment(value)) {
    if (segment.isWordLike !== false && segment.segment.startsWith(query)) {
      return true;
    }
  }
  return false;
}

export function settingsSearchTextMatches(value: string, query: string): boolean {
  const candidate = normalizeLowercaseStringOrEmpty(value).normalize("NFC");
  const normalizedQuery = normalizeLowercaseStringOrEmpty(query).normalize("NFC");
  if (!normalizedQuery) {
    return false;
  }
  if (normalizedQuery.length > 2) {
    return candidate.includes(normalizedQuery);
  }
  return settingsSearchHasWordPrefix(candidate, normalizedQuery);
}

// Keep the existing settings takeover, without exposing host administration
// pages whose Gateway APIs are not part of the Mirally product.
const SETTINGS_NAVIGATION_GROUPS = [
  { labelKey: null, routes: ["config", "profile", "model-providers", "appearance", "channels", "notifications", "about"] },
] as const satisfies readonly SettingsNavigationGroup[];

const NON_ADMIN_SETTINGS_ROUTES: ReadonlySet<NavigationRouteId> = new Set([
  "config",
  "profile",
  "appearance",
  "notifications",
  "model-providers",
  "channels",
  "about",
]);

export function isSettingsNavigationRouteVisible(
  routeId: NavigationRouteId,
  _canAdmin: boolean,
  _nativeDeviceSettings: NativeDeviceSettingsCapability | null = null,
): boolean {
  return NON_ADMIN_SETTINGS_ROUTES.has(routeId);
}

export function deviceSettingsGroupLabelKey(
  snapshot?: NativeDeviceSettingsSnapshot | null,
): string {
  const device = snapshot?.device;
  if (device?.platform === "macos") {
    return "nav.settingsGroupDevice";
  }
  if (device?.platform === "linux" || device?.platform === "windows") {
    return "nav.settingsGroupThisComputer";
  }
  if (device?.platform === "ios") {
    if (device.formFactor === "phone") {
      return "nav.settingsGroupThisIPhone";
    }
    if (device.formFactor === "pad") {
      return "nav.settingsGroupThisIPad";
    }
  }
  return "nav.settingsGroupThisDevice";
}

export function visibleSettingsNavigationGroups(
  canAdmin: boolean,
  nativeDeviceSettings: NativeDeviceSettingsCapability | null = null,
): readonly SettingsNavigationGroup[] {
  return SETTINGS_NAVIGATION_GROUPS.map((group) => ({
    labelKey: group.labelKey,
    routes: group.routes.filter((route) =>
      isSettingsNavigationRouteVisible(route, canAdmin, nativeDeviceSettings),
    ),
  })).filter((group) => group.routes.length > 0);
}

// Settings subpages render with settings chrome but stay out of the sidebar.
// Subpages with a visible owner keep that owner selected so users retain
// location context while completing the nested flow.
const SETTINGS_SUBPAGE_ROUTES: readonly NavigationRouteId[] = ["settings", "model-setup"];
export const SETTINGS_SEARCHABLE_SUBPAGE_ROUTES: readonly NavigationRouteId[] = [];
const SETTINGS_SUBPAGE_OWNER_ROUTES: Partial<
  Readonly<Record<NavigationRouteId, NavigationRouteId>>
> = {
  settings: "config",
  "model-setup": "model-providers",
};

const SETTINGS_NAVIGATION_ROUTES: ReadonlySet<NavigationRouteId> = new Set([
  ...SETTINGS_NAVIGATION_GROUPS.flatMap((group) => group.routes),
  ...SETTINGS_SUBPAGE_ROUTES,
]);

function navigationPresentation(icon: IconName, key: string): NavigationPresentation {
  return [icon, `tabs.${key}`, `subtitles.${key}`];
}

const NAVIGATION_PRESENTATION: Record<NavigationRouteId, NavigationPresentation> = {
  settings: ["settings", "nav.settings", "common.settingsSections"],
  "agents-home": navigationPresentation("bot", "agentsHome"),
  agents: navigationPresentation("bot", "agents"),
  activity: navigationPresentation("activity", "activity"),
  meetings: navigationPresentation("book", "meetings"),
  apps: navigationPresentation("layoutGrid", "apps"),
  portals: navigationPresentation("monitor", "portals"),
  approvals: navigationPresentation("badgeCheck", "approvals"),
  workboard: navigationPresentation("kanban", "workboard"),
  worktrees: navigationPresentation("folder", "worktrees"),
  channels: navigationPresentation("link", "channels"),
  connection: navigationPresentation("radio", "connection"),
  sessions: navigationPresentation("fileText", "sessions"),
  systems: navigationPresentation("monitor", "systems"),
  usage: navigationPresentation("coins", "usage"),
  cron: navigationPresentation("calendarClock", "cron"),
  tasks: navigationPresentation("listChecks", "tasks"),
  skills: navigationPresentation("bookOpenText", "skills"),
  "skill-settings": navigationPresentation("bookOpenText", "skills"),
  plugins: navigationPresentation("plug", "plugins"),
  "plugin-settings": navigationPresentation("plug", "plugins"),
  "skill-workshop": navigationPresentation("wrench", "skillWorkshop"),
  device: navigationPresentation("monitor", "device"),
  "device-permissions": navigationPresentation("shieldCheck", "devicePermissions"),
  devices: navigationPresentation("monitorSmartphone", "devices"),
  "cloud-workers": navigationPresentation("server", "cloudWorkers"),
  chat: navigationPresentation("messageSquare", "chat"),
  terminal: ["terminal", "terminal.title", "terminal.open"],
  dashboard: navigationPresentation("layoutDashboard", "chat"),
  dashboards: navigationPresentation("layoutDashboard", "dashboards"),
  custodian: navigationPresentation("lobster", "custodian"),
  config: ["settings", "nav.settings", "subtitles.config"],
  profile: navigationPresentation("circleUser", "profile"),
  communications: navigationPresentation("send", "communications"),
  appearance: navigationPresentation("palette", "appearance"),
  lobsterdex: navigationPresentation("bug", "lobsterdex"),
  automation: navigationPresentation("terminal", "automation"),
  mcp: navigationPresentation("wrench", "mcp"),
  memory: navigationPresentation("book", "memory"),
  search: navigationPresentation("search", "search"),
  talk: navigationPresentation("mic", "talk"),
  infrastructure: navigationPresentation("globe", "infrastructure"),
  labs: navigationPresentation("flaskConical", "labs"),
  updates: navigationPresentation("download", "updates"),
  about: navigationPresentation("fileText", "about"),
  "ai-agents": navigationPresentation("brain", "aiAgents"),
  "model-setup": navigationPresentation("spark", "modelSetup"),
  "model-providers": ["box", "routeTitles.modelProviders", "subtitles.modelProviders"],
  "memory-import": navigationPresentation("download", "memoryImport"),
  notifications: ["bell", "routeTitles.notifications", "subtitles.notifications"],
  security: navigationPresentation("shieldCheck", "security"),
  secrets: ["key", "tabs.secrets", "secretsStore.hint"],
  advanced: ["fileCode", "routeTitles.advanced", "subtitles.advanced"],
  debug: navigationPresentation("bug", "debug"),
  logs: navigationPresentation("scrollText", "logs"),
  plugin: navigationPresentation("plug", "plugin"),
  "new-session": ["plus", "newSession.title", "newSession.hint"],
};

type MirallyNavigationCopy = readonly [titleZh: string, titleEn: string, subtitleZh: string, subtitleEn: string];

// Mirally owns product language separately from the preserved upstream locale
// catalogs. New copy falls back to English outside Chinese locales.
const MIRALLY_NAVIGATION_COPY: Partial<Record<NavigationRouteId, MirallyNavigationCopy>> = {
  settings: ["设置", "Settings", "调整 Mirally 的体验与连接", "Customize Mirally and its connections"],
  chat: ["对话", "Chat", "与 Mirally 一起处理生活和工作中的事情", "Work through life and work with Mirally"],
  dashboard: ["对话", "Chat", "与 Mirally 一起处理生活和工作中的事情", "Work through life and work with Mirally"],
  "new-session": ["新对话", "New conversation", "从一件想聊、想做的事开始", "Start with something to discuss or do"],
  custodian: ["消息", "Inbox", "查看 Mirally 的主动联系与提醒投递", "Review messages and reminders from Mirally"],
  cron: ["提醒", "Reminders", "管理约定、到期时间与提醒状态", "Manage commitments, due dates, and reminder status"],
  activity: ["活动", "Activity", "选择允许 Mirally 了解的活动范围", "Choose which activities Mirally may understand"],
  memory: ["记忆", "Memory", "查看与管理 Mirally 记住的信息", "Review and manage what Mirally remembers"],
  plugins: ["扩展", "Extensions", "连接 Skill、MCP 与 Mirally 插件", "Connect Skills, MCP, and Mirally plugins"],
  channels: ["消息渠道", "Messaging channels", "从微信继续与 Mirally 交流", "Continue with Mirally from WeChat"],
  config: ["通用", "General", "查看 Mirally 的基本设置与数据位置", "Review Mirally settings and data locations"],
  profile: ["个性化", "Personalization", "管理你的偏好与 Mirally 的了解方式", "Manage your preferences and how Mirally understands you"],
  "model-providers": ["模型连接", "Model connection", "配置 Mirally 使用的模型与服务连接", "Configure the model and service Mirally uses"],
  "model-setup": ["模型连接", "Model connection", "配置 Mirally 使用的模型与服务连接", "Configure the model and service Mirally uses"],
  appearance: ["外观", "Appearance", "调整主题、色彩与界面偏好", "Adjust themes, colors, and interface preferences"],
  notifications: ["主动联系", "Proactive contact", "决定 Mirally 在什么时间、通过什么方式联系你", "Choose when and how Mirally may contact you"],
  about: ["关于与运行状态", "About and status", "查看 Mirally 的运行状态与能力边界", "Review Mirally status and supported capabilities"],
};

export function mirallyNavigationText(zh: string, en: string): string {
  return i18n.getLocale().startsWith("zh") ? zh : en;
}

export function isSettingsNavigationRoute(routeId: NavigationRouteId): boolean {
  return SETTINGS_NAVIGATION_ROUTES.has(routeId);
}

export function isSettingsTakeover(routeId: RouteId | undefined): boolean {
  return routeId !== undefined && isSettingsNavigationRoute(routeId);
}

export function settingsNavigationOwnerRoute(routeId: NavigationRouteId): NavigationRouteId {
  return SETTINGS_SUBPAGE_OWNER_ROUTES[routeId] ?? routeId;
}

export function navigationIconForRoute(routeId: NavigationRouteId): IconName {
  if (routeId === "custodian") return "bell";
  return NAVIGATION_PRESENTATION[routeId]?.[0] ?? "folder";
}

export function scheduleRoutePreload<TRouteId extends string>(
  timers: Map<EventTarget, ReturnType<typeof globalThis.setTimeout>>,
  routeId: TRouteId,
  event: Event,
  preload: ((routeId: TRouteId) => Promise<void> | void) | undefined,
  disabled = false,
  immediate = false,
) {
  if (disabled || !preload) {
    return;
  }
  const target = event.currentTarget;
  if (!target) {
    return;
  }
  const start = () => {
    timers.delete(target);
    try {
      void Promise.resolve(preload(routeId)).catch(() => undefined);
    } catch {
      // Preloading is opportunistic; navigation still handles real route errors.
    }
  };
  if (immediate) {
    cancelRoutePreload(timers, event);
    start();
    return;
  }
  if (!timers.has(target)) {
    timers.set(target, globalThis.setTimeout(start, 50));
  }
}

export function cancelRoutePreload(
  timers: Map<EventTarget, ReturnType<typeof globalThis.setTimeout>>,
  event: Event,
) {
  const target = event.currentTarget;
  if (!target) {
    return;
  }
  const timer = timers.get(target);
  if (timer !== undefined) {
    globalThis.clearTimeout(timer);
    timers.delete(target);
  }
}

export function titleForRoute(routeId: NavigationRouteId): string {
  const productCopy = MIRALLY_NAVIGATION_COPY[routeId];
  if (productCopy) return mirallyNavigationText(productCopy[0], productCopy[1]);
  const [, titleKey] = NAVIGATION_PRESENTATION[routeId];
  return t(titleKey);
}

/** Keep attention markers leftmost and use the Mirally product brand. */
export function formatDocumentTitle(options: {
  context: string;
  attentionCount?: number;
  gatewayDisconnected?: boolean;
}): string {
  const base = options.context.endsWith("Mirally")
    ? options.context
    : `${options.context} — Mirally`;
  if (options.gatewayDisconnected) {
    return `(${t("connection.disconnectedTitle")}) ${base}`;
  }
  if (options.attentionCount && options.attentionCount > 0) {
    return `(${options.attentionCount}) ${base}`;
  }
  return base;
}

export function settingsNavigationLabelForRoute(
  routeId: NavigationRouteId,
  snapshot?: NativeDeviceSettingsSnapshot | null,
): string {
  if (routeId === "device" && snapshot) {
    return t(deviceSettingsGroupLabelKey(snapshot));
  }
  return titleForRoute(routeId);
}

export function subtitleForRoute(routeId: NavigationRouteId): string {
  const productCopy = MIRALLY_NAVIGATION_COPY[routeId];
  if (productCopy) return mirallyNavigationText(productCopy[2], productCopy[3]);
  const subtitleKey = NAVIGATION_PRESENTATION[routeId][2];
  return t(subtitleKey);
}
