import { LitElement } from "lit";
import { I18nController } from "../i18n/lib/lit-controller.ts";

/** Lit base that refreshes the element when the active locale changes. */
export abstract class MirallyLitElement extends LitElement {
  protected readonly i18nController = new I18nController(this);
}

/** Mirally Lit base for components styled by the shared light-DOM stylesheet. */
export abstract class MirallyLightDomElement extends MirallyLitElement {
  override createRenderRoot() {
    return this;
  }
}

/** Light-DOM element whose host should not add a layout box around its render output. */
export abstract class MirallyLightDomContentsElement extends MirallyLightDomElement {
  override connectedCallback() {
    super.connectedCallback();
    this.style.display = "contents";
  }
}
