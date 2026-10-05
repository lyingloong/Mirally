import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { createUfoHarness } from "./harness.mjs";

export default definePluginEntry({
  id: "mirally-ufo",
  name: "Mirally UFO",
  description: "Native UFO HostAgent/AppAgent desktop execution",
  register(api) {
    api.registerAgentHarness(createUfoHarness(api));
  },
});
