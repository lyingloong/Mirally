import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";

export const page = definePage({
  ...routePageSpec("search"),
  component: () =>
    import("./search-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-search-page></mirally-search-page>`,
    })),
});
