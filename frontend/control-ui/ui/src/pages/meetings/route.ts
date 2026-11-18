import { definePage } from "@mirally/ui-router";
import { routePageSpec } from "../../app-route-paths.ts";

export const page = definePage({
  ...routePageSpec("meetings"),
  loaderDeps: (_context, { search }) => search,
  loader: (_context, { deps }) => deps,
  component: () => import("./meetings-page.ts").then((module) => module.meetingsPageComponent),
});
