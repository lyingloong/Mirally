import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";

export const page = definePage({
  ...routePageSpec("approvals"),
  component: () =>
    import("./approvals-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-approvals-page></mirally-approvals-page>`,
    })),
});
