import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";

export const page = definePage({
  ...routePageSpec("secrets"),
  component: () =>
    import("./secrets-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-secrets-page></mirally-secrets-page>`,
    })),
});
