import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";

export const page = definePage({
  ...routePageSpec("worktrees"),
  component: () =>
    import("./worktrees-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-worktrees-page></mirally-worktrees-page>`,
    })),
});
