import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";

export const page = definePage({
  ...routePageSpec("portals"),
  component: () =>
    import("./portals-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-portals-page></mirally-portals-page>`,
    })),
});
