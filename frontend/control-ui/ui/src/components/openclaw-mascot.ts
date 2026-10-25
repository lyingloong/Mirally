// Compatibility tag for retained shell markup and CSS. Product rendering is
// shared with Mirally's mascot; no upstream mascot artwork is loaded.
import { MirallyMascot } from "./mirally-mascot.ts";

if (!customElements.get("openclaw-mascot")) {
  customElements.define("openclaw-mascot", class extends MirallyMascot {});
}
