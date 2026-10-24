import {consume} from "@lit/context";
import {html, nothing, type PropertyValues, type TemplateResult} from "lit";
import {customElement, property, state} from "lit/decorators.js";
import {live} from "lit/directives/live.js";
import {applicationContext, type ApplicationContext} from "../../app/context.ts";
import {
  renderSettingsEmpty, renderSettingsPage, renderSettingsPageHeader, renderSettingsRow,
  renderSettingsSection, renderSettingsStatus, renderSettingsToggle, renderSettingsValue,
} from "../../components/settings-ui.ts";
import {renderSettingsWorkspace} from "../../components/settings-workspace.ts";
import {OpenClawLightDomElement} from "../../lit/openclaw-element.ts";
import {SubscriptionsController} from "../../lit/subscriptions-controller.ts";
import {errorMessage, openProductPage, requestProduct, rpcProduct, type ProductData} from "./api.ts";
import "./product.css";

type SettingsPageId = "general" | "profile" | "model" | "initiative" | "about";
type ModelSettings = {
  provider?: string; base_url?: string; api_format?: string; model_id?: string;
  api_key_configured?: boolean; max_tokens?: number; temperature?: number;
  image_enabled?: boolean; reasoning_enabled?: boolean;
};
type ProductSettings = {
  model: ModelSettings; memory_enabled: boolean; background_enabled: boolean;
  general_initiative_enabled: boolean; do_not_disturb: boolean;
  memory_semantics?: {max_calls_per_hour: number; max_tokens_per_day: number; max_output_tokens: number};
};
type Preference = {scope: string; key: string; value: unknown; revision: number};
type ProductStatus = {
  ready?: boolean; home?: string; database?: string; transcript_owner?: string;
  collector_started?: boolean; background_model_started?: boolean;
  now_utc_ms?: number; workers?: Record<string, unknown>;
};
/** Secrets are read directly from the password input and never enter a draft. */
type ModelDraft = {
  base_url: string; api_format: string; model_id: string; max_tokens: string;
  temperature: string; image_enabled: boolean; reasoning_enabled: boolean;
};
type BudgetDraft = {max_calls_per_hour: string; max_tokens_per_day: string; max_output_tokens: string};

const PAGE_COPY: Record<SettingsPageId, {title: string; subtitle: string}> = {
  general: {title: "通用", subtitle: "管理 Mirally 的日常使用方式与后台状态。"},
  profile: {title: "我的偏好", subtitle: "告诉 Mirally 你希望怎样协作，随时查看、修改或撤回。"},
  model: {title: "模型连接", subtitle: "配置 Mirally 对话与后台整理共用的模型服务。"},
  initiative: {title: "主动联系", subtitle: "决定 Mirally 何时可以整理上下文、主动给你留消息。"},
  about: {title: "关于 Mirally", subtitle: "覆盖生活与工作的 personal agent。"},
};

function textValue(value: unknown) {
  return typeof value === "string" ? value : JSON.stringify(value);
}

function modelDraft(model: ModelSettings): ModelDraft {
  return {
    base_url: model.base_url || "", model_id: model.model_id || "",
    api_format: ({"openai-completions": "openai-chat", anthropic: "anthropic-messages"} as Record<string, string>)[model.api_format || ""] || model.api_format || "openai-chat",
    max_tokens: String(model.max_tokens ?? 2048), temperature: String(model.temperature ?? 0.7),
    image_enabled: model.image_enabled === true, reasoning_enabled: model.reasoning_enabled === true,
  };
}

function workerState(status: ProductStatus | null, name: string): string {
  const container = status?.workers;
  const workers = container?.status && typeof container.status === "object"
    ? container.status as Record<string, unknown> : container;
  const worker = workers?.[name];
  if (!worker || typeof worker !== "object") return "尚未确认";
  const value = (worker as Record<string, unknown>).state;
  return ({running: "运行中", starting: "启动中", disabled: "已关闭", stopped: "已停止", paused: "已暂停",
    degraded: "部分可用", failed: "运行异常", cancelling: "正在停止", stop_unconfirmed: "停止待确认"} as Record<string, string>)[String(value)] || "尚未确认";
}

@customElement("mirally-settings-page")
export class MirallySettingsPage extends OpenClawLightDomElement {
  @consume({context: applicationContext, subscribe: true})
  private context!: ApplicationContext;

  @property({type: String}) page: string = "general";
  @state() private settings: ProductSettings | null = null;
  @state() private status: ProductStatus | null = null;
  @state() private preferences: Preference[] = [];
  @state() private model: ModelDraft | null = null;
  @state() private budget: BudgetDraft | null = null;
  @state() private preferenceKey = "";
  @state() private preferenceValue = "";
  @state() private preferenceStructured = false;
  private preferenceScope = "user/local";
  @state() private loading = false;
  @state() private busy = false;
  @state() private error = "";
  @state() private notice = "";
  private viewEpoch = 0;
  private loadEpoch = 0;
  private modelDirty = false;
  private budgetDirty = false;
  private connectedClient: unknown;
  private readonly hostReady = () => { if (!this.settings && !this.loading) void this.reload(); };
  private readonly subscriptions = new SubscriptionsController(this).watch(
    () => this.context?.gateway,
    (gateway, notify) => gateway.subscribe(notify),
    (gateway) => {
      const snapshot = gateway.snapshot;
      if (snapshot.phase === "connected" && snapshot.client !== this.connectedClient) {
        this.connectedClient = snapshot.client;
        if (this.hasUpdated) void this.reload();
      }
    },
  );

  override connectedCallback() {
    super.connectedCallback();
    window.addEventListener("mirally-host-ready", this.hostReady);
    if (this.hasUpdated) {
      this.busy = false;
      void this.reload();
    }
  }

  override disconnectedCallback() {
    ++this.viewEpoch; ++this.loadEpoch;
    this.clearSecret();
    window.removeEventListener("mirally-host-ready", this.hostReady);
    this.subscriptions.clear();
    super.disconnectedCallback();
  }

  override updated(changed: PropertyValues) {
    if (changed.has("page")) {
      ++this.viewEpoch;
      this.busy = false;
      this.error = ""; this.notice = "";
      this.clearSecret();
      void this.reload();
    }
  }

  private get pageId(): SettingsPageId {
    return Object.hasOwn(PAGE_COPY, this.page) ? this.page as SettingsPageId : "general";
  }

  private clearSecret() {
    const input = this.querySelector<HTMLInputElement>('[data-testid="mirally-settings-model-key"]');
    if (input) input.value = "";
  }

  private async reload() {
    const request = ++this.loadEpoch, view = this.viewEpoch;
    this.loading = true; this.error = "";
    try {
      const [settings, status, preferences] = await Promise.all([
        rpcProduct<ProductSettings>("settings.get"), rpcProduct<ProductStatus>("system.status"),
        rpcProduct<{preferences: Preference[]}>("preference.list"),
      ]);
      if (!this.isConnected || view !== this.viewEpoch || request !== this.loadEpoch) return;
      this.settings = settings; this.status = status; this.preferences = preferences.preferences || [];
      if (!this.modelDirty) this.model = modelDraft(settings.model || {});
      if (!this.budgetDirty) {
        const saved = settings.memory_semantics;
        this.budget = {
          max_calls_per_hour: String(saved?.max_calls_per_hour ?? 4),
          max_tokens_per_day: String(saved?.max_tokens_per_day ?? 24000),
          max_output_tokens: String(saved?.max_output_tokens ?? 1500),
        };
      }
    } catch (error) {
      if (this.isConnected && view === this.viewEpoch && request === this.loadEpoch) this.error = errorMessage(error);
    } finally {
      if (this.isConnected && view === this.viewEpoch && request === this.loadEpoch) this.loading = false;
    }
  }

  private async mutate(action: () => Promise<unknown>, message: string, success?: () => void, refresh = true) {
    if (this.busy || this.loading) return;
    const view = this.viewEpoch;
    this.busy = true; this.error = ""; this.notice = "";
    try {
      await action();
      if (!this.isConnected || view !== this.viewEpoch) return;
      success?.();
      this.notice = message;
      if (refresh) await this.reload();
    } catch (error) {
      if (this.isConnected && view === this.viewEpoch) this.error = errorMessage(error);
    } finally {
      if (this.isConnected && view === this.viewEpoch) this.busy = false;
    }
  }

  private toggle(key: "memory_enabled" | "background_enabled" | "general_initiative_enabled" | "do_not_disturb", title: string, description: string, testId: string) {
    return renderSettingsRow({title, description, control: html`<span data-testid=${testId}>
      ${renderSettingsToggle({checked: this.settings?.[key] === true, disabled: this.busy || this.loading || !this.settings,
        ariaLabel: title, onChange: value => { void this.mutate(() => rpcProduct("settings.update", {patch: {[key]: value}}), "设置已保存。"); return false; }})}
    </span>`});
  }

  private field(label: string, control: TemplateResult, description?: string) {
    return renderSettingsRow({title: label, description, control, stackedOnNarrow: true});
  }

  private async navigate(page: string) {
    try { await openProductPage(page); }
    catch (error) { this.error = errorMessage(error); }
  }

  private savePreference(event: SubmitEvent) {
    event.preventDefault();
    const key = this.preferenceKey.trim(), text = this.preferenceValue.trim();
    if (!key || !text) return;
    let value: unknown = text;
    if (this.preferenceStructured) {
      try { value = JSON.parse(text); }
      catch { this.error = "该偏好需要有效的 JSON 内容，请修改后重试。"; return; }
    }
    const scope = this.preferenceScope;
    void this.mutate(async () => {
      await rpcProduct("preference.set", {key, value, scope});
      await rpcProduct("source.set", {source_id:`preference:${scope}:${key}`,enabled:true,reason:"user_saved_preference"});
    }, "偏好已保存，后续使用会采用新内容。", () => {
      this.preferenceKey = ""; this.preferenceValue = "";
      this.preferenceScope = "user/local"; this.preferenceStructured = false;
    });
  }

  private generalPage() {
    return html`
      ${renderSettingsSection({title: "了解你与陪伴你"}, html`
        ${this.toggle("memory_enabled", "使用记忆", "在对话中使用已启用的个人记忆。关闭后不再读取这些记忆。", "mirally-settings-memory")}
        ${renderSettingsRow({title: "电脑活动", description: "查看已有记录、采集范围与运行状态，开始采集前需确认当前范围。",
          control: html`<button class="btn btn--sm" data-testid="mirally-settings-open-activity" @click=${() => void this.navigate("activity")}>管理活动</button>`})}
        ${renderSettingsRow({title: "记忆管理", description: "检索记忆与来源，查看候选内容。",
          control: html`<button class="btn btn--sm" data-testid="mirally-settings-open-memory" @click=${() => void this.navigate("memory")}>查看记忆</button>`})}
      `)}
      ${renderSettingsSection({title: "运行状态"}, html`
        ${renderSettingsRow({title: "Mirally", control: renderSettingsStatus({kind: this.status?.ready ? "ok" : "warn", label: this.status?.ready ? "已就绪" : "尚未确认"})})}
        ${renderSettingsRow({title: "活动采集", control: renderSettingsValue(workerState(this.status, "activity"))})}
        ${renderSettingsRow({title: "记忆维护", control: renderSettingsValue(workerState(this.status, "memory"))})}
        ${renderSettingsRow({title: "后台整理", control: renderSettingsValue(workerState(this.status, "semantic"))})}
      `)}
    `;
  }

  private profilePage() {
    return html`
      <form @submit=${(event: SubmitEvent) => this.savePreference(event)}>
        ${renderSettingsSection({title: "添加或修改偏好", description: "保存即允许 Mirally 使用此偏好；相同名称会更新并启用已有偏好。变更可能停止正在进行的回复，以免继续使用旧内容。"}, html`
          ${this.field("名称", html`<input class="settings-input" aria-label="偏好名称" data-testid="mirally-settings-preference-key" .value=${live(this.preferenceKey)} @input=${(event: Event) => {this.preferenceKey = (event.target as HTMLInputElement).value;}} required maxlength="512" ?disabled=${this.busy || this.loading}>`)}
          ${this.field("内容", html`<textarea class="settings-input" aria-label="偏好内容" data-testid="mirally-settings-preference-value" .value=${live(this.preferenceValue)} @input=${(event: Event) => {this.preferenceValue = (event.target as HTMLTextAreaElement).value;}} required maxlength="12000" rows="3" ?disabled=${this.busy || this.loading}></textarea>`, this.preferenceStructured ? "此偏好包含结构化数据，请以 JSON 格式编辑。" : undefined)}
          ${renderSettingsRow({title: "", control: html`<button class="btn primary" type="submit" data-testid="mirally-settings-preference-save" ?disabled=${this.busy || this.loading}>保存偏好</button>`})}
        `)}
      </form>
      ${renderSettingsSection({title: "正在使用的偏好", count: this.preferences.length}, this.preferences.length
        ? this.preferences.map(preference => renderSettingsRow({title: preference.key, description: textValue(preference.value),
          control: html`<button class="btn btn--sm" data-testid="mirally-settings-preference-edit" ?disabled=${this.busy || this.loading}
            @click=${() => {this.preferenceKey = preference.key; this.preferenceValue = textValue(preference.value) || ""; this.preferenceScope = preference.scope; this.preferenceStructured = typeof preference.value !== "string";}}>编辑</button>
            <button class="btn btn--sm" data-testid="mirally-settings-preference-revoke" ?disabled=${this.busy || this.loading}
            @click=${() => void this.mutate(() => rpcProduct("source.set", {source_id: `preference:${preference.scope}:${preference.key}`, enabled: false, reason: "user_withdrew_preference"}), "已撤回该偏好的使用。重新保存同名偏好可以启用。")}>撤回使用</button>`}))
        : renderSettingsEmpty("还没有保存的偏好。你也可以在对话中明确让 Mirally 记住你的偏好。"))}
    `;
  }

  private modelInput(name: "base_url" | "model_id" | "max_tokens" | "temperature", label: string, type = "text", options: {min?: number; max?: number; step?: string; placeholder?: string} = {}) {
    return this.field(label, html`<input class="settings-input" type=${type} aria-label=${label} data-testid=${`mirally-settings-model-${name==='model_id'?'id':name.replaceAll("_", "-")}`}
      .value=${live(this.model?.[name] || "")} ?disabled=${this.busy || this.loading} required
      min=${options.min ?? nothing} max=${options.max ?? nothing} step=${options.step ?? nothing} placeholder=${options.placeholder ?? ""}
      @input=${(event: Event) => {if (this.model) {this.model = {...this.model, [name]: (event.target as HTMLInputElement).value}; this.modelDirty = true;}}}>`);
  }

  private saveModel(event: SubmitEvent) {
    event.preventDefault();
    if (!this.model || this.busy || this.loading) return;
    const secretInput = this.querySelector<HTMLInputElement>('[data-testid="mirally-settings-model-key"]');
    const secret = secretInput?.value || "";
    if (secretInput) secretInput.value = "";
    const draft = this.model;
    const model: ProductData = {base_url: draft.base_url.trim(), api_format: draft.api_format, model_id: draft.model_id.trim(),
      max_tokens: Number(draft.max_tokens), temperature: Number(draft.temperature), image_enabled: draft.image_enabled, reasoning_enabled: draft.reasoning_enabled};
    if (secret) model.api_key = secret;
    void this.mutate(() => rpcProduct("settings.update", {patch: {model}}), "模型连接已保存。实际请求会在使用该模型时进行。", () => {this.modelDirty = false;});
  }

  private modelPage() {
    return html`<form data-testid="mirally-settings-model-form" @submit=${(event: SubmitEvent) => this.saveModel(event)}>
      ${renderSettingsSection({title: "连接信息", description: "填写你自己的服务地址和模型 ID。留空 API Key 会保留已保存的凭据。"}, html`
        ${this.field("接口格式", html`<select class="settings-input" aria-label="接口格式" data-testid="mirally-settings-model-format"
          .value=${live(this.model?.api_format || "openai-chat")} ?disabled=${this.busy || this.loading}
          @change=${(event: Event) => {if (this.model) {this.model = {...this.model, api_format: (event.target as HTMLSelectElement).value}; this.modelDirty = true;}}}>
          <option value="openai-chat">OpenAI Chat Completions 兼容</option><option value="openai-responses">OpenAI Responses 兼容</option><option value="anthropic-messages">Anthropic Messages 兼容</option>
        </select>`)}
        ${this.modelInput("base_url", "服务地址", "url", {placeholder: "https://api.example.com/v1"})}
        ${this.modelInput("model_id", "模型 ID", "text", {placeholder: "填写服务支持的模型 ID"})}
        ${this.field("API Key", html`<input class="settings-input" type="password" aria-label="API Key" data-testid="mirally-settings-model-key" autocomplete="new-password" spellcheck="false"
          placeholder=${this.settings?.model.api_key_configured ? "已保存，留空保持不变" : "填写 API Key（本地服务可留空）"} ?disabled=${this.busy || this.loading}>`, this.settings?.model.api_key_configured ? "凭据已保存；输入内容发送后会立即从此输入框清空。" : "输入内容发送后会立即清空，保存失败时请重新输入。")}
      `)}
      ${renderSettingsSection({title: "模型能力", description: "请按服务实际支持的能力配置。"}, html`
        ${this.modelInput("max_tokens", "最大输出 token", "number", {min: 128, max: 262144, step: "1"})}
        ${this.modelInput("temperature", "温度", "number", {min: 0, max: 2, step: "0.1"})}
        ${renderSettingsRow({title: "图片输入", description: "让对话将图片材料提交给支持视觉的模型。", control: html`<span data-testid="mirally-settings-model-images">${renderSettingsToggle({checked: this.model?.image_enabled === true, disabled: this.busy || this.loading || !this.model, ariaLabel: "图片输入", onChange: value => {if (this.model) {this.model = {...this.model, image_enabled: value}; this.modelDirty = true;}}})}</span>`})}
        ${renderSettingsRow({title: "推理模式", description: "向兼容服务请求 medium 级别推理。", control: html`<span data-testid="mirally-settings-model-reasoning">${renderSettingsToggle({checked: this.model?.reasoning_enabled === true, disabled: this.busy || this.loading || !this.model, ariaLabel: "推理模式", onChange: value => {if (this.model) {this.model = {...this.model, reasoning_enabled: value}; this.modelDirty = true;}}})}</span>`})}
      `)}
      ${renderSettingsSection({}, renderSettingsRow({title: this.settings?.model.model_id ? `当前保存：${this.settings.model.model_id}` : "尚未配置模型",
        control: html`<button class="btn primary" type="submit" data-testid="mirally-settings-model-save" ?disabled=${this.busy || this.loading || !this.model}>保存连接</button>`}))}
    </form>`;
  }

  private initiativePage() {
    const enabled = this.settings?.background_enabled && this.settings?.general_initiative_enabled && !this.settings?.do_not_disturb;
    return html`
      ${renderSettingsSection({title: "联系与打扰"}, html`
        ${this.toggle("background_enabled", "后台整理", "允许使用当前模型整理活动与记忆；会按下方预算产生模型请求。", "mirally-settings-background")}
        ${this.toggle("general_initiative_enabled", "主动联系", "结合已有上下文判断是否留一条有用的消息，消息进入收件箱。需要同时启用后台整理。", "mirally-settings-initiative")}
        ${this.toggle("do_not_disturb", "免打扰", "暂停主动联系；你明确创建的定时提醒仍会进入收件箱。", "mirally-settings-dnd")}
        ${renderSettingsRow({title: "当前联系状态", control: renderSettingsStatus({kind: enabled ? "ok" : "muted", label: enabled ? "允许按需联系" : "主动联系已暂停"})})}
      `)}
      <form @submit=${(event: SubmitEvent) => {event.preventDefault(); if (!this.budget) return;
        const budget = Object.fromEntries(Object.entries(this.budget).map(([key, value]) => [key, Number(value)]));
        void this.mutate(() => rpcProduct("memory.configure", {patch: budget}), "后台整理预算已保存。", () => {this.budgetDirty = false;});}}>
        ${renderSettingsSection({title: "记忆整理预算", description: "这些预算约束记忆整理。主动联系另有最多每四小时检查一次的限制，不能在这里调整。"}, html`
          ${([["max_calls_per_hour", "每小时最多调用次数", 0, 1000], ["max_tokens_per_day", "每天最多 token", 0, 10000000], ["max_output_tokens", "单次最大输出 token", 128, 262144]] as const).map(([name, label, min, max]) => this.field(label, html`<input class="settings-input" type="number" aria-label=${label} data-testid=${`mirally-settings-budget-${name.replaceAll("_", "-")}`}
            .value=${live(this.budget?.[name] || "")} required min=${min} max=${max} step="1" ?disabled=${this.busy || this.loading}
            @input=${(event: Event) => {if (this.budget) {this.budget = {...this.budget, [name]: (event.target as HTMLInputElement).value}; this.budgetDirty = true;}}}>`))}
          ${renderSettingsRow({title: "", control: html`<button class="btn primary" type="submit" data-testid="mirally-settings-budget-save" ?disabled=${this.busy || this.loading || !this.budget}>保存预算</button>`})}
        `)}
      </form>
    `;
  }

  private aboutPage() {
    return html`
      ${renderSettingsSection({title: "Mirally", description: "在生活与工作中持续理解、记住和陪伴你，提供有分寸的主动协作。"}, html`
        ${renderSettingsRow({title: "产品框架", control: renderSettingsValue("Mirally")})}
        ${renderSettingsRow({title: "会话内核", control: renderSettingsValue(this.status?.transcript_owner === "dsh" ? "DSH（同仓维护）" : "尚未确认")})}
        ${renderSettingsRow({title: "主模型", control: renderSettingsValue(this.settings?.model.model_id || "尚未配置")})}
        ${renderSettingsRow({title: "当前运行状态", control: renderSettingsStatus({kind: this.status?.ready ? "ok" : "warn", label: this.status?.ready ? "已就绪" : "尚未确认"})})}
      `)}
      ${renderSettingsSection({title: "诊断信息"}, renderSettingsRow({title: "本机运行详情", description: "目录、后台 worker 状态与时间，用于排查问题。",
        stacked: true, control: html`<details data-testid="mirally-settings-diagnostics"><summary>查看诊断信息</summary><pre class="mirally-product-debug">${JSON.stringify(this.status, null, 2)}</pre></details>`}))}
      ${renderSettingsSection({title: "退出", danger: true}, renderSettingsRow({title: "退出 Mirally", description: "停止当前 Mirally 宿主。后台任务与对话会一并停止。",
        control: html`<button class="btn btn--sm" data-testid="mirally-settings-quit" ?disabled=${this.busy || this.loading}
          @click=${() => void this.mutate(() => requestProduct("/api/shutdown", {method: "POST", body: "{}"}), "Mirally 正在退出。", undefined, false)}>退出 Mirally</button>`}))}
    `;
  }

  override render() {
    const page = this.pageId, copy = PAGE_COPY[page];
    const content = page === "model" ? this.modelPage() : page === "profile" ? this.profilePage() : page === "initiative" ? this.initiativePage() : page === "about" ? this.aboutPage() : this.generalPage();
    return html`
      ${renderSettingsPageHeader({...copy, actions: html`<button class="btn btn--sm" data-testid="mirally-settings-refresh" ?disabled=${this.busy || this.loading}
        @click=${() => void this.reload()}>${this.loading ? "正在刷新…" : "刷新"}</button>`})}
      ${renderSettingsWorkspace(renderSettingsPage(html`<div class="mirally-settings-content" data-testid=${`mirally-settings-${page}`} aria-busy=${this.loading || this.busy ? "true" : "false"}>
        ${this.error ? html`<div class="callout danger" role="alert" data-testid="mirally-settings-error">${this.error}</div>` : nothing}
        ${this.notice ? html`<div class="callout" role="status" data-testid="mirally-settings-notice">${this.notice}</div>` : nothing}
        ${!this.settings ? renderSettingsEmpty(this.loading ? "正在读取 Mirally 设置…" : "尚未读取到设置，请刷新后重试。") : content}
      </div>`))}
    `;
  }
}
