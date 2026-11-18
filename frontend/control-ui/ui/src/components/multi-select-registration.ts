import { MultiSelect } from "./multi-select.ts";

if (!customElements.get("mirally-multi-select")) {
  customElements.define("mirally-multi-select", MultiSelect);
}

declare global {
  interface HTMLElementTagNameMap {
    "mirally-multi-select": MultiSelect;
  }
}
