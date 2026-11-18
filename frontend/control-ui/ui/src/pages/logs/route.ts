import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";

export const page = definePage({
  ...routePageSpec("logs"),
  component: () =>
    import("./logs-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-logs-page></mirally-logs-page>`,
    })),
});
