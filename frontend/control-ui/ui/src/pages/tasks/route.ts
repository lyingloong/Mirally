import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";

export const page = definePage({
  ...routePageSpec("tasks"),
  component: () =>
    import("./tasks-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-tasks-page></mirally-tasks-page>`,
    })),
});
