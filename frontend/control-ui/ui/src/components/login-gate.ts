// Product recovery uses the same local Mirally Host and the existing shell styling.
import { html, nothing } from "lit";
import { property } from "lit/decorators.js";
import type { ThemeMascot } from "../../../packages/gateway-protocol/src/theme.ts";
import type { LoginFailureFeedbackParams } from "./login-gate-feedback.ts";
import { canReloadControlUiDocument } from "../app/document-reload-guard.ts";
import { i18n } from "../i18n/index.ts";
import "../lib/toast.ts";
import { OpenClawLightDomContentsElement } from "../lit/openclaw-element.ts";
import { mirallyMark } from "./mirally-mark.ts";

type LoginGateProps = LoginFailureFeedbackParams & {
  mascot?: ThemeMascot;
  resourceBasePath: string;
  gatewayUrl: string;
  secret: string;
  showGatewaySecret: boolean;
  onGatewayUrlChange: (value: string) => void;
  onSecretChange: (value: string) => void;
  onToggleGatewaySecret: () => void;
  onConnect: () => void;
  onOpenGatewaySettings?: () => void;
};

function renderLoginGate(props: LoginGateProps) {
  const zh = i18n.getLocale().startsWith("zh");
  const reconnecting = props.reconnectPending;
  const copy = zh ? {
    title: reconnecting ? "正在连接 Mirally…" : "暂时无法连接 Mirally",
    summary: "请确认本机 Mirally 仍在运行。若已退出或连接已过期，请在项目目录运行 .\\run.ps1，再使用新打开的窗口。",
    retry: "重新连接", reload: "刷新页面", details: "连接详情",
  } : {
    title: reconnecting ? "Connecting to Mirally…" : "Mirally is not connected",
    summary: "Check that Mirally is running on this computer. If it has stopped or this connection has expired, run .\\run.ps1 in the project folder and use the newly opened window.",
    retry: "Reconnect", reload: "Refresh page", details: "Connection details",
  };
  return html`<div class="login-gate"><openclaw-toast-host></openclaw-toast-host>
    <div class="login-gate__card" data-mode="status" data-mirally-connection>
      <header class="login-gate__brand">
        <span class="login-gate__logo login-gate__logo--neutral" aria-hidden="true">${mirallyMark}</span>
        <span class="login-gate__brand-name">Mirally</span>
      </header>
      <section class="login-gate__body login-gate__failure" role="status" aria-live="polite">
        <h1 class="login-gate__failure-title">${copy.title}</h1>
        <p class="login-gate__failure-summary">${copy.summary}</p>
        <div class="login-gate__form">
          <button class="btn primary login-gate__connect" ?disabled=${reconnecting} @click=${props.onConnect}>${copy.retry}</button>
          <button class="btn" @click=${() => {if (canReloadControlUiDocument(true)) location.reload();}}>${copy.reload}</button>
        </div>
        ${props.lastError ? html`<details class="login-gate__failure-detail"><summary>${copy.details}</summary>
          <div class="login-gate__failure-raw mono">${props.lastError}</div></details>` : nothing}
      </section>
    </div>
  </div>`;
}

class LoginGate extends OpenClawLightDomContentsElement {
  @property({attribute: false}) props?: LoginGateProps;
  override render() { return this.props ? renderLoginGate(this.props) : nothing; }
}
if (!customElements.get("openclaw-login-gate")) customElements.define("openclaw-login-gate", LoginGate);
