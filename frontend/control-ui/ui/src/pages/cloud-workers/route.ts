import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";
import type { ApplicationContext } from "../../app/context.ts";

export const page = definePage({
  ...routePageSpec("cloud-workers"),
  loader: (context: ApplicationContext) => context.runtimeConfig.ensureLoaded(),
  component: () =>
    import("./cloud-workers-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-cloud-workers-page></mirally-cloud-workers-page>`,
    })),
});
