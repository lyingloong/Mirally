import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";

export const page = definePage({
  ...routePageSpec("about"),
  component: () =>
    import("./about-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-about-page></mirally-about-page>`,
    })),
});
