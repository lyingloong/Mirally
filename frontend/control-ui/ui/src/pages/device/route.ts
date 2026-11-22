import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";

export const page = definePage({
  ...routePageSpec("device"),
  component: () =>
    import("./device-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-device-page></mirally-device-page>`,
    })),
});

export const permissionsPage = definePage({
  ...routePageSpec("device-permissions"),
  component: () =>
    import("./permissions-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-device-permissions-page></mirally-device-permissions-page>`,
    })),
});
