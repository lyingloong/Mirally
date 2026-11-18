import { definePage } from "@mirally/ui-router";
import { html } from "lit";
import { routePageSpec } from "../../app-route-paths.ts";
import type { ApplicationContext } from "../../app/context.ts";
import type { SkillWorkshopRouteData } from "./proposals.ts";
import { loadSkillWorkshopMode } from "./storage.ts";

export const page = definePage({
  ...routePageSpec("skill-workshop"),
  component: () =>
    import("./skill-workshop-page.ts").then(() => ({
      render: (data: unknown) => html`
        <mirally-skill-workshop-page
          .data=${data as SkillWorkshopRouteData | undefined}
        ></mirally-skill-workshop-page>
      `,
    })),
  loader: async (context: ApplicationContext) => {
    const { loadSkillWorkshopProposals, createSkillWorkshopState, skillWorkshopRouteData } =
      await import("./proposals.ts");
    const state = createSkillWorkshopState();
    state.skillWorkshopMode = loadSkillWorkshopMode();
    await loadSkillWorkshopProposals(state, context, { force: true });
    return skillWorkshopRouteData(state);
  },
});
