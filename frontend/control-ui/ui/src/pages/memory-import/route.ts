import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";

export const page = definePage({
  ...routePageSpec("memory-import"),
  component: () =>
    import("./memory-import-page.ts").then(() => ({
      header: true,
      render: () => html`<mirally-memory-import-page></mirally-memory-import-page>`,
    })),
});
