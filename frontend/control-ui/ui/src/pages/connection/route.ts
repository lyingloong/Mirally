import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";

export const page = definePage({
  ...routePageSpec("connection"),
  component: () =>
    import("./connection-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-connection-page></mirally-connection-page>`,
    })),
});
