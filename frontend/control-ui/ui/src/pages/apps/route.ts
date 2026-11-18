import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";

export const page = definePage({
  ...routePageSpec("apps"),
  component: () =>
    import("./apps-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-apps-page></mirally-apps-page>`,
    })),
});
