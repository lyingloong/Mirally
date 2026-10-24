import { normalizeLowercaseStringOrEmpty } from "@openclaw/normalization-core/string-coerce";
import type { GatewayBrowserClient } from "../api/gateway.ts";
import type {
  AgentsListResult,
  CronJobsListResult,
  CronCompactJob,
  GatewaySessionRow,
} from "../api/types.ts";
import {
  navigationIconForRoute,
  SIDEBAR_NAV_ROUTES,
  SETTINGS_SEARCHABLE_SUBPAGE_ROUTES,
  titleForRoute,
  settingsNavigationLabelForRoute,
  subtitleForRoute,
  visibleSettingsNavigationGroups,
} from "../app-navigation.ts";
import type { RouteId } from "../app-route-paths.ts";
import type { NativeDeviceSettingsCapability } from "../app/native-device-settings.ts";
import { t } from "../i18n/index.ts";
import { registerCommandPaletteEnglish } from "../i18n/locales/en-command-palette.ts";
import { loadModelCatalog, modelCatalogRefreshError } from "../lib/model-catalog-store.ts";
import type { IconName } from "./icons.ts";

registerCommandPaletteEnglish();


type CommandPaletteCatalogCategory =
  | "agents"
  | "apps"
  | "automations"
  | "models"
  | "plugins"
  | "settings"
  | "skills";

type CommandPaletteCatalogItem = {
  id: string;
  label: string;
  icon: IconName;
  category: CommandPaletteCatalogCategory;
  routeId: RouteId;
  search?: string;
  hash?: string;
  agentId?: string;
  description?: string;
  searchText?: string;
};

export type CommandPaletteItem = Omit<CommandPaletteCatalogItem, "routeId" | "category"> & {
  category: "search" | "navigation" | "chats" | "messages" | CommandPaletteCatalogCategory;
  action: string;
  session?: GatewaySessionRow;
};

export function commandPaletteCategoryLabel(category: string): string {
  switch (category) {
    case "search":
      return t("palette.categories.search");
    case "navigation":
      return t("palette.categories.navigation");
    case "skills":
      return t("palette.categories.skills");
    case "agents":
      return t("palette.items.agents");
    case "apps":
      return t("palette.items.apps");
    case "automations":
      return titleForRoute("cron");
    case "models":
      return titleForRoute("model-providers");
    case "plugins":
      return titleForRoute("plugins");
    case "settings":
      return t("palette.items.settings");
    case "chats":
      return t("sessionsView.title");
    case "messages":
      return t("palette.categories.messages");
    default:
      return category;
  }
}

const CATALOG_SEARCH_LIMIT = 10;

function getCommandPaletteBaseItems(
  _desktopAvailable: boolean,
  _custodianAvailable: boolean,
): CommandPaletteItem[] {
  const routes: readonly RouteId[] = ["new-session", ...SIDEBAR_NAV_ROUTES, "config"];
  return routes.map((routeId) => ({
    id: `nav-${routeId}`,
    label: routeId === "config" ? titleForRoute("settings") : titleForRoute(routeId),
    description: subtitleForRoute(routeId),
    icon: navigationIconForRoute(routeId),
    category: "navigation",
    action: `nav:${routeId}`,
  }));
}

export function filterCommandPaletteItems(params: {
  query: string;
  includeSlashCommands: boolean;
  sessionItems: readonly CommandPaletteItem[];
  catalogItems: readonly CommandPaletteItem[];
  desktopAvailable: boolean;
  custodianAvailable: boolean;
}): CommandPaletteItem[] {
  const baseItems = getCommandPaletteBaseItems(
    params.desktopAvailable,
    params.custodianAvailable,
  ).filter((item) => params.includeSlashCommands || item.category !== "search");
  if (!params.query) {
    return baseItems;
  }
  const query = normalizeLowercaseStringOrEmpty(params.query);
  const matchRank = (item: CommandPaletteItem) => {
    const label = normalizeLowercaseStringOrEmpty(item.label);
    if (label === query) {
      return 3;
    }
    if (label.startsWith(query)) {
      return 2;
    }
    return label.includes(query) ||
      normalizeLowercaseStringOrEmpty(item.description).includes(query) ||
      normalizeLowercaseStringOrEmpty(item.searchText).includes(query)
      ? 1
      : 0;
  };
  const baseMatches = baseItems.filter((item) => matchRank(item) > 0);
  const catalogMatches = params.catalogItems
    .map((item) => ({ item, rank: matchRank(item) }))
    .filter(({ rank }) => rank > 0)
    .toSorted(
      (left, right) => right.rank - left.rank || left.item.label.localeCompare(right.item.label),
    )
    .slice(0, CATALOG_SEARCH_LIMIT)
    .map(({ item }) => item);
  return [...params.sessionItems, ...baseMatches, ...catalogMatches];
}

export function toCommandPaletteItems(
  items: readonly CommandPaletteCatalogItem[],
): CommandPaletteItem[] {
  return items.map((item) => ({
    id: item.id,
    label: item.label,
    icon: item.icon,
    category: item.category,
    action: `nav:${item.routeId}`,
    search: item.search,
    hash: item.hash,
    agentId: item.agentId,
    description: item.description,
    searchText: item.searchText,
  }));
}

export function getStaticCommandPaletteCatalogItems(
  canAdmin: boolean,
  nativeDeviceSettings: NativeDeviceSettingsCapability | null = null,
): CommandPaletteCatalogItem[] {
  return visibleSettingsNavigationGroups(canAdmin, nativeDeviceSettings)
    .flatMap((group) => group.routes)
    .concat(SETTINGS_SEARCHABLE_SUBPAGE_ROUTES)
    .map((routeId) => ({
      id: `settings-${routeId}`,
      label: settingsNavigationLabelForRoute(routeId),
      icon: navigationIconForRoute(routeId),
      category: "settings" as const,
      routeId,
      description: subtitleForRoute(routeId),
      searchText: routeId,
    }));
}

export async function loadCommandPaletteCatalogItems(params: {
  client: GatewayBrowserClient;
  agentId: string;
  agents: () => Promise<AgentsListResult | null>;
  methodAvailable: (method: string) => boolean;
}): Promise<{
  items: CommandPaletteCatalogItem[];
  modelRequestFailed: boolean;
  modelSearchError: string | null;
}> {
  const requestIfAvailable = async <T>(
    method: string,
    requestParams: unknown,
  ): Promise<T | null> =>
    params.methodAvailable(method)
      ? params.client.request<T>(method, requestParams).catch(() => null)
      : null;
  const [automations, models] = await Promise.all([
    requestIfAvailable<CronJobsListResult<CronCompactJob>>("cron.list", {
      includeDisabled: true,
      limit: 200,
      offset: 0,
      sortBy: "name",
      sortDir: "asc",
      compact: true,
    }),
    loadModelCatalog(params.client, { agentId: params.agentId }).catch(() => null),
  ]);

  const items: CommandPaletteCatalogItem[] = [
    ...(automations?.jobs ?? []).map((job) => ({
      id: `automation-${job.id}`,
      label: job.displayName ?? job.name,
      icon: "calendarClock" as const,
      category: "automations" as const,
      routeId: "cron" as const,
      searchText: [job.id, job.declarationKey, job.name, job.agentId].filter(Boolean).join(" "),
    })),
    ...(models?.models ?? []).map((model) => ({
      // Both IDs can contain separators; selection needs a lossless pair.
      id: `model-${JSON.stringify([model.provider, model.id])}`,
      label: model.name || model.id,
      icon: "brain" as const,
      category: "models" as const,
      routeId: "model-providers" as const,
      description: model.provider,
      searchText: [model.id, model.provider, model.alias, model.tags?.join(" ")]
        .filter(Boolean)
        .join(" "),
    })),
  ];
  return {
    items,
    modelRequestFailed: models === null,
    modelSearchError: models ? modelCatalogRefreshError(models) : t("palette.modelSearchFailed"),
  };
}
