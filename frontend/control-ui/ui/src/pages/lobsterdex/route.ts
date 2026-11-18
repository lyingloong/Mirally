import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";

export const page = definePage({
  ...routePageSpec("lobsterdex"),
  component: () =>
    import("./lobsterdex-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-lobsterdex-page></mirally-lobsterdex-page>`,
    })),
});
