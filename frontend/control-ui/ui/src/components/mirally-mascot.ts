import { css, html, LitElement } from "lit";
import { property } from "lit/decorators.js";
import { mirallyMark } from "./mirally-mark.ts";
import type { MascotMood } from "./mascot-pose.ts";

/** Shared product identity for setup, reconnect, assistant and welcome surfaces. */
export class MirallyMascot extends LitElement {
  static override styles = css`
    :host {
      display: inline-block;
      width: var(--openclaw-mascot-size, 120px);
      height: var(--openclaw-mascot-size, 120px);
      color: var(--accent, #52b9ef);
      pointer-events: none;
      line-height: 0;
      contain: layout style;
    }
    .mark { display: block; width: 100%; height: 100%; animation: breathe 4s ease-in-out infinite; }
    svg { display: block; width: 100%; height: 100%; }
    :host([mood="working"]) .mark, :host([mood="thinking"]) .mark { animation-duration: 2s; }
    :host([mood="sleepy"]) .mark { animation-duration: 6s; }
    :host([data-paused]) .mark { animation-play-state: paused; }
    @keyframes breathe { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-3px); } }
    @media (prefers-reduced-motion: reduce) { .mark { animation: none !important; } }
  `;

  @property({ reflect: true }) mood: MascotMood = "idle";
  @property({ type: Number }) size = 120;
  @property({ type: Boolean }) tease = false;
  private observer: IntersectionObserver | null = null;
  private catchAnimation: Animation | null = null;
  private visible = true;
  private readonly updatePlayback = () => {
    this.toggleAttribute("data-paused", !this.visible || document.hidden);
  };

  override connectedCallback() {
    super.connectedCallback();
    this.setAttribute("data-mirally-mascot", "companion-m");
    this.setAttribute("aria-hidden", "true");
    document.addEventListener("visibilitychange", this.updatePlayback);
    if (typeof IntersectionObserver !== "undefined") {
      this.observer = new IntersectionObserver(entries => {
        this.visible = entries.some(entry => entry.isIntersecting);
        this.updatePlayback();
      });
      this.observer.observe(this);
    }
    this.updatePlayback();
  }

  override disconnectedCallback() {
    document.removeEventListener("visibilitychange", this.updatePlayback);
    this.observer?.disconnect();
    this.observer = null;
    this.catchAnimation?.cancel();
    this.catchAnimation = null;
    super.disconnectedCallback();
  }

  protected override updated() {
    const size = Number.isFinite(this.size) && this.size > 0 ? this.size : 120;
    this.style.setProperty("--openclaw-mascot-size", `${size}px`);
  }

  // Existing assistant-panel consumers use this public gesture hook.
  catchOnce() {
    if (!this.isConnected || document.hidden || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    this.catchAnimation?.cancel();
    this.catchAnimation = this.renderRoot.querySelector(".mark")?.animate?.(
      [{transform: "rotate(0deg)"}, {transform: "rotate(-8deg) translateY(-4px)"}, {transform: "rotate(0deg)"}],
      {duration: 450, easing: "ease-in-out"},
    ) ?? null;
  }

  override render() {
    return html`<span class="mark">${mirallyMark}</span>`;
  }
}

if (!customElements.get("mirally-mascot")) customElements.define("mirally-mascot", MirallyMascot);
