import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";
import type { ApplicationContext } from "../../app/context.ts";

export const page = definePage({
  ...routePageSpec("profile"),
  loader: (context: ApplicationContext) => {
    // Warm the agents list so the hero identity renders without a flash.
    void context.agents.ensureList();
  },
  component: () =>
    import("./profile-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-profile-page></mirally-profile-page>`,
    })),
});
