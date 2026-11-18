import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";

export const page = definePage({
  ...routePageSpec("debug"),
  component: () =>
    import("./debug-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-debug-page></mirally-debug-page>`,
    })),
});
