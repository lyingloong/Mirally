import { definePage } from "@mirally/ui-router";
import { routePageSpec } from "../../app-route-paths.ts";

export const page = definePage({
  ...routePageSpec("agents-home"),
  component: () => import("./agents-home-page.ts"),
});
