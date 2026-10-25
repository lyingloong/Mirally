import { css, html, LitElement, nothing, type PropertyValues } from "lit";
import { property, state } from "lit/decorators.js";
import { inferControlUiPublicAssetPath } from "../app/public-assets.ts";
import { patchSettings } from "../app/settings.ts";

/** Composer visitor uses Mirally's existing otter; native three-form pets stay independent. */
class MirallyComposerCompanion extends LitElement {
  static override styles = css`
    :host { position: absolute; z-index: 2; bottom: calc(100% - 4px); right: 10%; width: 52px; height: 64px; pointer-events: none; }
    button { background: none; border: 0; padding: 0; cursor: pointer; width: 100%; height: 100%; pointer-events: auto; }
    img { width: 100%; height: 100%; object-fit: contain; animation: breathe 4s ease-in-out infinite; }
    :host([data-mode="busy"]) img { animation-duration: 1.6s; }
    @keyframes breathe { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-3px); } }
    @media (prefers-reduced-motion: reduce) { img { animation: none; } }
  `;
  @property({ attribute: false }) seed = 0;
  @property({ attribute: false }) mode = "idle";
  @property({ attribute: false }) runOutcome = "ok";
  @property({ attribute: false }) visitsEnabled = true;
  @property({ attribute: false }) residentEnabled = true;
  @property({ attribute: false }) onVisitsDisabled: () => void = () => undefined;
  @state() private visiting = false;
  @state() private dismissed = false;
  private timer: number | undefined;

  private schedule(visible: boolean, delay: number) {
    window.clearTimeout(this.timer);
    this.timer = window.setTimeout(() => {
      this.visiting = visible;
      this.schedule(!visible, visible ? 25_000 : 60_000);
    }, delay);
  }

  override connectedCallback() {
    super.connectedCallback();
    this.schedule(true, 6_000 + Math.abs(this.seed % 4_000));
  }
  override disconnectedCallback() {
    window.clearTimeout(this.timer);
    super.disconnectedCallback();
  }
  protected override updated(changed: PropertyValues<this>) {
    this.setAttribute("data-mode", this.mode);
    if (changed.has("visitsEnabled") && this.visitsEnabled) this.dismissed = false;
  }
  private disableVisits(event: MouseEvent) {
    event.preventDefault();
    this.dismissed = true;
    patchSettings({lobsterPetVisits: false}); // Preserve existing preference storage.
    this.onVisitsDisabled();
  }
  override render() {
    if (!this.visitsEnabled || !this.residentEnabled || this.dismissed || document.hidden || (!this.visiting && this.mode === "idle")) return nothing;
    return html`<button type="button" data-mirally-mascot="otter" aria-label="Mirally 伙伴，点击暂时收起，右键关闭页面来访"
      @click=${() => {this.visiting = false; this.dismissed = true;}}
      @contextmenu=${(event: MouseEvent) => this.disableVisits(event)}>
      <img src=${inferControlUiPublicAssetPath("mirally-pet-otter.svg")} alt="Mirally 伙伴" />
    </button>`;
  }
}
if (!customElements.get("mirally-composer-companion")) customElements.define("mirally-composer-companion", MirallyComposerCompanion);
