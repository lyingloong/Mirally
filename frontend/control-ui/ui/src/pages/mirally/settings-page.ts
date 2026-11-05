import {consume} from "@lit/context";
import {html, nothing, type PropertyValues, type TemplateResult} from "lit";
import {customElement, eventOptions, property, state} from "lit/decorators.js";
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
import {modelDraft, deepSeekPreset, adoptDiscoveredModel, isDeepSeekEndpoint, modelCapabilityText,
  type ModelSettings, type ModelDraft, type DiscoveredModel} from "./model-settings.ts";
import "./product.css";
import "./capabilities-settings.ts";
import "./speech-settings.ts";

type SettingsPageId = "general" | "profile" | "model" | "initiative" | "about";
type ProductSettings = {
  model: ModelSettings; memory_enabled: boolean; background_enabled: boolean;
  model_connections?: Record<string, {name: string; model: ModelSettings}>;
  model_catalogs?: Record<string, ModelSettings[]>;
  model_routes?: {chat: string; background: string};
  capabilities?: {web_enabled: boolean; search_connection: string};
  general_initiative_enabled: boolean; do_not_disturb: boolean;
  action_approval_mode: "ask" | "trusted"; weixin_auto_approve_enabled: boolean;
  memory_semantics?: {max_calls_per_hour: number; max_tokens_per_day: number; max_output_tokens: number};
};
type Preference = {scope: string; key: string; value: unknown; revision: number};
type ProductStatus = {
  ready?: boolean; home?: string; database?: string; transcript_owner?: string;
  collector_started?: boolean; background_model_started?: boolean;
  now_utc_ms?: number; workers?: Record<string, unknown>;
};
type BudgetDraft = {max_calls_per_hour: string; max_tokens_per_day: string; max_output_tokens: string};
type CalendarSettings = {time_zone: string | null; confirmed: boolean; detected_time_zone: string | null;
  channels: {desktop: boolean; weixin: boolean}};

const PAGE_COPY: Record<SettingsPageId, {title: string; subtitle?: string}> = {
  general: {title: "通用"},
  profile: {title: "我的偏好"},
  model: {title: "模型连接"},
  initiative: {title: "主动联系"},
  about: {title: "关于 Mirally", subtitle: "覆盖生活与工作的 personal agent。"},
};

function textValue(value: unknown) {
  return typeof value === "string" ? value : JSON.stringify(value);
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
  @state() private calendarSettings: CalendarSettings | null = null;
  @state() private timeZoneDraft = '';
  @state() private status: ProductStatus | null = null;
  @state() private preferences: Preference[] = [];
  @state() private closeBehavior: 'tray' | 'exit' = 'tray';
  @state() private hideCompanionFromCapture = true;
  @state() private model: ModelDraft | null = null;
  @state() private connectionId = 'default';
  @state() private connectionName = '默认连接';
  @state() private deletingConnection = false;
  @state() private clearModelKey = false;
  @state() private discoveredModels: DiscoveredModel[] | null = null;
  @state() private discoveredSelection = '';
  @state() private catalogDraft: ModelDraft | null = null;
  @state() private catalogEditing = false;
  @state() private deletingModel = '';
  @state() private modelTest: {text: string; model: string; elapsed_ms: number; mode: string} | null = null;
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
      const [settings, status, preferences, presentation, calendarSettings] = await Promise.all([
        rpcProduct<ProductSettings>("settings.get"), rpcProduct<ProductStatus>("system.status"),
        rpcProduct<{preferences: Preference[]}>("preference.list"),
        requestProduct<{preferences: Record<string, unknown>}>("/api/ui-preferences"),
        rpcProduct<CalendarSettings>("calendar.settings.get"),
      ]);
      if (!this.isConnected || view !== this.viewEpoch || request !== this.loadEpoch) return;
      this.settings = settings; this.status = status; this.preferences = preferences.preferences || [];
      this.calendarSettings = calendarSettings;
      this.timeZoneDraft = calendarSettings.time_zone || calendarSettings.detected_time_zone || '';
      // A refresh may observe another window's service or credential changes.
      // Probe results belong to their original saved connection, not this one.
      this.discoveredModels = null; this.modelTest = null;
      this.closeBehavior = presentation.preferences?.['mirally.window.closeBehavior'] === 'exit' ? 'exit' : 'tray';
      this.hideCompanionFromCapture = presentation.preferences?.['mirally.companion.hideFromCapture'] !== false;
      if (!this.modelDirty) {
        if (this.connectionId !== 'default' && !settings.model_connections?.[this.connectionId]) this.connectionId = 'default';
        this.model = modelDraft(this.savedConnectionModel);
        this.connectionName = this.connectionId === 'default' ? '默认连接' : settings.model_connections![this.connectionId].name;
      }
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

  private toggle(key: "memory_enabled" | "background_enabled" | "general_initiative_enabled" | "do_not_disturb" | "weixin_auto_approve_enabled", title: string, description: string | undefined, testId: string) {
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
    }, "偏好已保存。", () => {
      this.preferenceKey = ""; this.preferenceValue = "";
      this.preferenceScope = "user/local"; this.preferenceStructured = false;
    });
  }

  private generalPage() {
    return html`
      <mirally-capabilities-settings></mirally-capabilities-settings>
      <mirally-speech-settings></mirally-speech-settings>
      ${renderSettingsSection({title: "日期与时间"}, html`
        ${this.field("时区", html`<form @submit=${(event: SubmitEvent) => {
          event.preventDefault(); const value = this.timeZoneDraft.trim(); if (!value) return;
          void this.mutate(() => rpcProduct("calendar.settings.update", {time_zone:value}), "时区已保存。");
        }}><input class="settings-input" aria-label="时区" list="mirally-time-zones" data-testid="mirally-time-zone"
          .value=${live(this.timeZoneDraft)} @input=${(event: Event) => {this.timeZoneDraft = (event.target as HTMLInputElement).value;}}
          placeholder="Asia/Shanghai" required ?disabled=${this.busy || this.loading}>
          <datalist id="mirally-time-zones">${(typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : ['Asia/Shanghai', 'UTC']).map(zone => html`<option value=${zone}></option>`)}</datalist>
          <button class="btn" type="submit" ?disabled=${this.busy || this.loading}>${this.calendarSettings?.confirmed ? '保存' : '确认时区'}</button></form>`,
          this.calendarSettings?.confirmed ? undefined : '请确认日历和提醒使用的时区。')}
        ${(['desktop','weixin'] as const).map(channel => renderSettingsRow({title:channel === 'desktop' ? '桌面提醒' : '微信提醒', control:renderSettingsToggle({
          checked:this.calendarSettings?.channels?.[channel] === true, disabled:this.busy || this.loading || !this.calendarSettings,
          ariaLabel:channel === 'desktop' ? '桌面提醒' : '微信提醒', onChange:value => {
            void this.mutate(() => rpcProduct('calendar.settings.update',{channels:{[channel]:value}}), '提醒方式已保存。'); return false;
          }})}))}
      `)}
      ${renderSettingsSection({title: "会话操作权限"}, html`
        ${renderSettingsRow({title: "默认确认方式", description: "自动批准允许模型直接执行命令、操作浏览器和读取屏幕。", control: html`<select class="settings-input" aria-label="默认确认方式" data-testid="mirally-action-approval-mode" .value=${live(this.settings?.action_approval_mode || 'ask')} ?disabled=${this.busy || this.loading || !this.settings}
          @change=${(event: Event) => {const value=(event.target as HTMLSelectElement).value; void this.mutate(() => rpcProduct("settings.update", {patch: {action_approval_mode: value}}), "会话操作权限已保存。");}}>
          <option value="ask">逐次确认</option><option value="trusted">自动批准（持续）</option></select>`})}
      `)}
      ${renderSettingsSection({title: "桌面窗口"}, html`${renderSettingsRow({title: "关闭主窗口时",
        control: html`<select class="settings-input" aria-label="关闭主窗口时" data-testid="mirally-settings-close-behavior" .value=${live(this.closeBehavior)} ?disabled=${this.busy || this.loading}
          @change=${(event: Event) => {const value=(event.target as HTMLSelectElement).value;void this.mutate(()=>requestProduct('/api/ui-preferences',{method:'POST',body:JSON.stringify({key:'mirally.window.closeBehavior',value})}), '关闭方式已保存。');}}>
          <option value="tray">隐藏到系统托盘</option><option value="exit">退出 Mirally</option>
        </select>`})}
        ${renderSettingsRow({title: "截图/录屏时隐藏伙伴", description: "适用于支持 Windows 窗口排除的截图与录屏软件。",
          control: html`<span data-testid="mirally-settings-hide-companion-from-capture">${renderSettingsToggle({checked: this.hideCompanionFromCapture, disabled: this.busy || this.loading,
            ariaLabel: "截图/录屏时隐藏伙伴", onChange: value => {void this.mutate(() => requestProduct('/api/ui-preferences', {method: 'POST', body: JSON.stringify({key: 'mirally.companion.hideFromCapture', value})}), '截图与录屏显示方式已保存。'); return false; }})}</span>`})}`)}
      ${renderSettingsSection({title: "了解你与陪伴你"}, html`
        ${this.toggle("memory_enabled", "使用记忆", undefined, "mirally-settings-memory")}
        ${renderSettingsRow({title: "电脑活动",
          control: html`<button class="btn btn--sm" data-testid="mirally-settings-open-activity" @click=${() => void this.navigate("activity")}>管理活动</button>`})}
        ${renderSettingsRow({title: "记忆管理",
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
        ${renderSettingsSection({title: "添加或修改偏好", description: "保存后会使用此偏好；更新可能中断正在进行的回复。"}, html`
          ${this.field("名称", html`<input class="settings-input" aria-label="偏好名称" data-testid="mirally-settings-preference-key" .value=${live(this.preferenceKey)} @input=${(event: Event) => {this.preferenceKey = (event.target as HTMLInputElement).value;}} required maxlength="512" ?disabled=${this.busy || this.loading}>`)}
          ${this.field("内容", html`<textarea class=${this.preferenceStructured ? "settings-input" : "settings-input mirally-prose-input"} aria-label="偏好内容" data-testid="mirally-settings-preference-value" .value=${live(this.preferenceValue)} @input=${(event: Event) => {this.preferenceValue = (event.target as HTMLTextAreaElement).value;}} required maxlength="12000" rows="3" ?disabled=${this.busy || this.loading}></textarea>`, this.preferenceStructured ? "此偏好包含结构化数据，请以 JSON 格式编辑。" : undefined)}
          ${renderSettingsRow({title: "", control: html`<button class="btn primary" type="submit" data-testid="mirally-settings-preference-save" ?disabled=${this.busy || this.loading}>保存偏好</button>`})}
        `)}
      </form>
      ${renderSettingsSection({title: "正在使用的偏好", count: this.preferences.length}, this.preferences.length
        ? this.preferences.map(preference => renderSettingsRow({title: preference.key, description: textValue(preference.value),
          control: html`<button class="btn btn--sm" data-testid="mirally-settings-preference-edit" ?disabled=${this.busy || this.loading}
            @click=${() => {this.preferenceKey = preference.key; this.preferenceValue = textValue(preference.value) || ""; this.preferenceScope = preference.scope; this.preferenceStructured = typeof preference.value !== "string";}}>编辑</button>
            <button class="btn btn--sm" data-testid="mirally-settings-preference-revoke" ?disabled=${this.busy || this.loading}
            @click=${() => void this.mutate(() => rpcProduct("source.set", {source_id: `preference:${preference.scope}:${preference.key}`, enabled: false, reason: "user_withdrew_preference"}), "已撤回该偏好。")}>撤回使用</button>`}))
        : renderSettingsEmpty("还没有保存的偏好。"))}
    `;
  }

  private changeModel(patch: Partial<ModelDraft>) {
    if (!this.model) return;
    if ((patch.base_url !== undefined && patch.base_url !== this.model.base_url)
      || (patch.api_format !== undefined && patch.api_format !== this.model.api_format)) this.discoveredModels = null;
    this.model = {...this.model, ...patch};
    this.modelDirty = true; this.modelTest = null;
  }

  private get savedConnectionModel(): ModelSettings {
    return this.connectionId === 'default' ? this.settings?.model || {} : this.settings?.model_connections?.[this.connectionId]?.model || {};
  }

  private selectConnection(id: string) {
    this.clearSecret(); this.connectionId = id; this.deletingConnection = false; this.clearModelKey = false;
    this.discoveredModels = null; this.discoveredSelection = ''; this.catalogDraft = null; this.deletingModel = ''; this.modelTest = null; this.error = ''; this.notice = '';
    this.connectionName = id === 'new' ? '' : id === 'default' ? '默认连接' : this.settings?.model_connections?.[id]?.name || '';
    this.model = modelDraft(this.savedConnectionModel); this.modelDirty = id === 'new';
  }

  private connectionSection() {
    const connections = [{id: 'default', name: '默认连接'}, ...Object.entries(this.settings?.model_connections || {}).map(([id, item]) => ({id, name: item.name}))];
    const uses = Object.entries(this.settings?.model_routes || {chat: 'default', background: 'default'}).filter(([,id])=>id===this.connectionId).map(([purpose])=>purpose==='chat'?'对话':'后台整理与主动联系');
    if (this.settings?.capabilities?.web_enabled && this.settings.capabilities.search_connection===this.connectionId) uses.push('网页研究');
    return renderSettingsSection({}, html`
      ${renderSettingsRow({title:'编辑连接', description:uses.length ? `当前用于：${uses.join('、')}` : undefined, control:html`<div class="mirally-product-actions mirally-model-connection-picker">
        <select class="settings-input" aria-label="编辑连接" data-testid="mirally-settings-connection-select" .value=${live(this.connectionId)} ?disabled=${this.busy||this.loading||this.modelDirty||!!this.catalogDraft}
          @change=${(event:Event)=>this.selectConnection((event.target as HTMLSelectElement).value)}>${connections.map(item=>html`<option value=${item.id} .selected=${item.id === this.connectionId}>${item.name}</option>`)}${this.connectionId==='new'?html`<option value="new" .selected=${true}>新连接</option>`:nothing}</select>
        <button class="btn btn--sm" type="button" data-testid="mirally-settings-connection-new" ?disabled=${this.busy||this.loading||this.modelDirty||!!this.catalogDraft} @click=${()=>this.selectConnection('new')}>添加连接</button>
        ${this.modelDirty?html`<button class="btn btn--sm" type="button" data-testid="mirally-settings-connection-discard" ?disabled=${this.busy||this.loading} @click=${()=>this.selectConnection(this.connectionId==='new'?'default':this.connectionId)}>放弃更改</button>`:nothing}
        ${!['default','new'].includes(this.connectionId)?html`<button class="btn btn--sm" type="button" data-testid="mirally-settings-connection-delete" ?disabled=${this.busy||this.loading||this.modelDirty||!!this.catalogDraft||uses.length>0} @click=${()=>{this.deletingConnection=true;}}>删除连接</button>`:nothing}
      </div>`})}
      ${this.deletingConnection ? renderSettingsRow({title:'确认删除连接', description:'将删除此连接与本地保存的凭据，操作无法撤销。', control:html`
        <button class="btn danger btn--sm" type="button" data-testid="mirally-settings-connection-delete-confirm" ?disabled=${this.busy||this.loading} @click=${()=>{const id=this.connectionId;void this.mutate(()=>rpcProduct('model.connection.remove',{connection_id:id}),'连接已删除。',()=>this.selectConnection('default'));}}>确认删除</button>
        <button class="btn btn--sm" type="button" ?disabled=${this.busy||this.loading} @click=${()=>{this.deletingConnection=false;}}>取消</button>`}) : nothing}

    `);
  }

  private routingSection() {
    const connections = [{id: 'default', name: '默认连接'}, ...Object.entries(this.settings?.model_connections || {}).map(([id, item]) => ({id, name: item.name}))];
    return renderSettingsSection({title: '模型用途'}, html`
      ${(['chat','background'] as const).map(purpose=>this.field(purpose==='chat'?'对话使用':'后台整理与主动联系使用',html`<select class="settings-input" aria-label=${purpose==='chat'?'对话使用':'后台整理与主动联系使用'} data-testid=${`mirally-settings-route-${purpose}`} .value=${live(this.settings?.model_routes?.[purpose]||'default')} ?disabled=${this.busy||this.loading||this.modelDirty||!!this.catalogDraft}
        @change=${(event:Event)=>{const id=(event.target as HTMLSelectElement).value;void this.mutate(()=>rpcProduct('model.routes.set',{routes:{[purpose]:id}}),'用途已保存。');}}>${connections.map(item=>html`<option value=${item.id} .selected=${item.id === (this.settings?.model_routes?.[purpose] || 'default')}>${item.name}</option>`)}</select>`))}
    `);
  }

  private modelInput(name: "base_url" | "model_id" | "max_tokens" | "context_window" | "temperature", label: string, type = "text", options: {min?: number; max?: number; step?: string; placeholder?: string; description?: string} = {}) {
    return this.field(label, html`<input class="settings-input" type=${type} aria-label=${label} data-testid=${`mirally-settings-model-${name==='model_id'?'id':name.replaceAll("_", "-")}`}
      .value=${live(this.model?.[name] || "")} ?disabled=${this.busy || this.loading} ?required=${name !== "model_id"}
      min=${options.min ?? nothing} max=${options.max ?? nothing} step=${options.step ?? nothing} placeholder=${options.placeholder ?? ""}
      @input=${(event: Event) => this.changeModel({[name]: (event.target as HTMLInputElement).value})}>`, options.description);
  }

  @eventOptions({capture: true})
  private revealInvalidModelField(event: Event) {
    if (!(event.target instanceof HTMLElement)) return;
    const disclosure = event.target.closest<HTMLDetailsElement>('details');
    if (disclosure) disclosure.open = true;
    // Keep native validation feedback and focus after the field becomes visible.
  }

  private saveModel(event: SubmitEvent) {
    event.preventDefault();
    if (!this.model || this.busy || this.loading) return;
    const secretInput = this.querySelector<HTMLInputElement>('[data-testid="mirally-settings-model-key"]');
    const secret = secretInput?.value || "";
    if (secretInput) secretInput.value = "";
    const draft = this.model;
    const model: ProductData = {provider: draft.provider, base_url: draft.base_url.trim(), api_format: draft.api_format, model_id: draft.model_id.trim(),
      max_tokens: Number(draft.max_tokens), context_window: Number(draft.context_window), temperature: Number(draft.temperature),
      image_enabled: draft.image_enabled, reasoning_effort: draft.reasoning_effort, reasoning_enabled: !['off'].includes(draft.reasoning_effort)};
    if (this.clearModelKey) model.api_key = '';
    else if (secret) model.api_key = secret;
    let savedId = this.connectionId;
    void this.mutate(async () => {
      const saved = await rpcProduct<{connection_id:string}>('model.connection.save', {model, name:this.connectionName, ...(this.connectionId==='new'?{}:{connection_id:this.connectionId})});
      savedId = saved.connection_id;
    }, "模型连接已保存。", () => {this.connectionId=savedId;this.clearModelKey=false;this.modelDirty = false; this.modelTest = null; this.discoveredModels = null;});
  }

  private async discoverModels() {
    if (this.modelDirty) return;
    const view = this.viewEpoch, load = this.loadEpoch;
    this.discoveredModels = null;
    await this.mutate(async () => {
      const result = await requestProduct<{models: DiscoveredModel[]; truncated?: boolean}>("/api/models/discover", {method: "POST", body: JSON.stringify({connection_id:this.connectionId})});
      if (this.isConnected && view === this.viewEpoch && load === this.loadEpoch) this.discoveredModels = result.models;
    }, "模型列表已读取。", undefined, false);
  }

  private async testModel() {
    if (this.modelDirty) return;
    const view = this.viewEpoch, load = this.loadEpoch;
    this.modelTest = null;
    await this.mutate(async () => {
      const result = await requestProduct<{text: string; model: string; elapsed_ms: number; mode: string}>("/api/models/test", {method: "POST", body: JSON.stringify({connection_id:this.connectionId})});
      if (this.isConnected && view === this.viewEpoch && load === this.loadEpoch) this.modelTest = result;
    }, "文字测试已完成。", undefined, false);
  }

  private modelPage() {
    const deepseek = isDeepSeekEndpoint(this.model?.base_url || "");
    const selected = this.discoveredModels?.find(item => item.id === this.discoveredSelection);
    const levels = deepseek ? ['default', 'off', 'low', 'high', 'max'] : ['default', 'off', 'low', 'medium', 'high', 'max'];
    if (this.model?.reasoning_effort && !levels.includes(this.model.reasoning_effort)) levels.push(this.model.reasoning_effort);
    const labels: Record<string, string> = {default: '服务默认', off: '关闭', low: 'Low · 低', medium: 'Medium · 中', high: 'High · 高', max: 'Max · 最高'};
    return html`${this.connectionSection()}<form data-testid="mirally-settings-model-form" @invalid=${this.revealInvalidModelField} @submit=${(event: SubmitEvent) => this.saveModel(event)}>
      ${renderSettingsSection({title: "连接信息"}, html`
        ${this.connectionId !== 'default' ? this.field('名称',html`<input class="settings-input" data-testid="mirally-settings-connection-name" aria-label="连接名称" required maxlength="128" .value=${live(this.connectionName)} ?disabled=${this.busy||this.loading} @input=${(event:Event)=>{this.connectionName=(event.target as HTMLInputElement).value;this.modelDirty=true;}}>`):nothing}
        ${this.field("服务预设", html`<select class="settings-input" aria-label="服务预设" data-testid="mirally-settings-model-preset"
          .value=${live(deepseek ? 'deepseek' : 'custom')} ?disabled=${this.busy || this.loading}
          @change=${(event: Event) => {if (!this.model) return; const choice=(event.target as HTMLSelectElement).value;
            if(choice==='deepseek') {this.changeModel(deepSeekPreset(this.model)); this.clearSecret();}
            else this.changeModel({provider: 'openai-compatible', base_url: '', model_id: ''});}}>
          <option value="custom" .selected=${!deepseek}>自定义 / 兼容服务</option><option value="deepseek" .selected=${deepseek}>DeepSeek 官方</option>
        </select>`, "更换服务时请同时填写该服务对应的 API Key。")}
        ${this.field("接口格式", html`<select class="settings-input" aria-label="接口格式" data-testid="mirally-settings-model-format"
          .value=${live(this.model?.api_format || "openai-chat")} ?disabled=${this.busy || this.loading}
          @change=${(event: Event) => this.changeModel({api_format: (event.target as HTMLSelectElement).value})}>
          <option value="openai-chat" .selected=${!this.model?.api_format || this.model.api_format === 'openai-chat'}>OpenAI Chat Completions 兼容</option><option value="openai-responses" .selected=${this.model?.api_format === 'openai-responses'}>OpenAI Responses 兼容</option><option value="anthropic-messages" .selected=${this.model?.api_format === 'anthropic-messages'}>Anthropic Messages 兼容</option>
        </select>`)}
        ${this.modelInput("base_url", "服务地址", "url", {placeholder: "https://api.example.com/v1"})}
        ${this.modelInput("model_id", "默认模型 ID", "text", {description: "可留空，稍后从模型列表添加。"})}
        ${this.field("API Key", html`<input class="settings-input" type="password" aria-label="API Key" data-testid="mirally-settings-model-key" autocomplete="new-password" spellcheck="false"
          ?disabled=${this.busy || this.loading}
          @input=${() => {this.modelDirty = true; this.modelTest = null;}}>`, this.savedConnectionModel.api_key_configured ? "已保存；留空保持不变。" : "本地服务可留空。")}
        ${this.savedConnectionModel.api_key_configured ? renderSettingsRow({title:'清除已保存凭据', description:'保存连接时删除此连接的 API Key。',control:html`<span data-testid="mirally-settings-model-clear-key"><input type="checkbox" aria-label="清除已保存凭据" .checked=${live(this.clearModelKey)} ?disabled=${this.busy||this.loading} @change=${(event: Event)=>{this.clearModelKey=(event.target as HTMLInputElement).checked;this.modelDirty=true;}}></span>`}) : nothing}
      `)}
      <details class="mirally-product-disclosure" data-testid="mirally-settings-model-advanced"><summary>模型参数</summary>
      ${renderSettingsSection({}, html`
        ${this.modelInput("max_tokens", "最大输出 token", "number", {min: 128, max: 262144, step: "1", description: "包含模型的推理 token。"})}
        ${this.modelInput("context_window", "上下文窗口 token", "number", {min: 128, max: 10000000, step: "1", description: "填写模型支持的上下文窗口大小。"})}
        ${this.modelInput("temperature", "温度", "number", {min: 0, max: 2, step: "0.1", description: deepseek ? "思考模式可能忽略此参数。" : undefined})}
        ${renderSettingsRow({title: "图片输入", description: "仅在模型支持图片时启用。", control: html`<span data-testid="mirally-settings-model-images"><input type="checkbox" aria-label="图片输入" .checked=${live(this.model?.image_enabled === true)} ?disabled=${this.busy || this.loading || !this.model} @change=${(event: Event) => this.changeModel({image_enabled: (event.target as HTMLInputElement).checked})}></span>`})}
        ${this.field("推理强度", html`<select class="settings-input" aria-label="推理强度" data-testid="mirally-settings-model-reasoning"
          .value=${live(this.model?.reasoning_effort || 'default')} ?disabled=${this.busy || this.loading || !this.model}
          @change=${(event: Event) => this.changeModel({reasoning_effort: (event.target as HTMLSelectElement).value})}>
          ${levels.map(level => html`<option value=${level} .selected=${level === (this.model?.reasoning_effort || 'default')}>${labels[level]}</option>`)}
        </select>`, "按模型支持的档位选择。")}
      `)}
      </details>
      ${renderSettingsSection({}, renderSettingsRow({title: this.savedConnectionModel.model_id ? `当前保存：${this.savedConnectionModel.model_id}` : "尚未配置模型", description: this.modelDirty ? "有未保存的更改。" : undefined,
        control: html`<button class="btn primary" type="submit" data-testid="mirally-settings-model-save" ?disabled=${this.busy || this.loading || !this.model}>保存连接</button>`}))}
    </form>
    ${this.routingSection()}
    ${this.catalogSection()}
    ${renderSettingsSection({title: "检查连接", description: "文字测试会发送固定短消息，可能产生费用。"}, html`
      ${renderSettingsRow({title: "模型列表与文字测试", control: html`
        <button class="btn btn--sm" type="button" data-testid="mirally-settings-model-discover" ?disabled=${this.busy || this.loading || this.modelDirty || !this.savedConnectionModel.base_url} @click=${() => void this.discoverModels()}>读取可用模型</button>
        <button class="btn btn--sm" type="button" data-testid="mirally-settings-model-test" ?disabled=${this.busy || this.loading || this.modelDirty || !this.savedConnectionModel.model_id} @click=${() => void this.testModel()}>测试文字回复</button>`})}
      ${this.modelTest ? html`<div data-testid="mirally-settings-model-test-result">${renderSettingsRow({title: `${this.modelTest.mode === 'test' ? '离线测试模型' : '文字请求完成'} · ${this.modelTest.model} · ${(this.modelTest.elapsed_ms / 1000).toFixed(1)} 秒`, description: this.modelTest.text || "服务已响应，但未返回文字。可提高最大输出 token，或关闭推理后再试。"})}</div>` : nothing}
      ${this.discoveredModels ? this.discoveredModels.length ? html`
        ${this.field("服务提供的模型", html`<select class="settings-input" aria-label="服务提供的模型" data-testid="mirally-settings-model-catalog" .value=${live(this.discoveredSelection)} ?disabled=${this.busy || this.loading}
          @change=${(event: Event) => {this.discoveredSelection=(event.target as HTMLSelectElement).value;}}>
          <option value="">选择模型…</option>${this.discoveredModels.map(item=>html`<option value=${item.id}>${item.name}</option>`)}
        </select>`, selected ? modelCapabilityText(selected) : undefined)}
        ${selected ? renderSettingsRow({title: "添加所选模型",
          control: html`<button class="btn btn--sm" type="button" data-testid="mirally-settings-model-adopt" ?disabled=${this.busy || this.loading || !!this.catalogDraft}
            @click=${() => {if (this.model) {this.catalogEditing=false; this.catalogDraft=adoptDiscoveredModel({...modelDraft(this.savedConnectionModel),image_enabled:false,reasoning_effort:'default'}, selected);}}}>添加到模型列表</button>`}) : nothing}
      ` : renderSettingsEmpty("服务返回了空模型列表，仍可手动填写模型 ID。") : nothing}
    `)}`;
  }

  private catalogSection() {
    if (this.connectionId === 'new') return nothing;
    const entries = this.settings?.model_catalogs?.[this.connectionId] || [];
    const draft = this.catalogDraft;
    const set = (patch: Partial<ModelDraft>) => { if (this.catalogDraft) this.catalogDraft = {...this.catalogDraft, ...patch}; };
    const input = (key: 'model_id'|'max_tokens'|'context_window'|'temperature', label: string, type = 'text', min?: number, max?: number, step?: string) => this.field(label, html`
      <input class="settings-input" type=${type} aria-label=${`附加模型${label}`} data-testid=${`mirally-catalog-${key}`} .value=${live(draft?.[key] || '')}
        ?disabled=${this.busy || this.loading || (key === 'model_id' && this.catalogEditing)} required min=${min ?? nothing} max=${max ?? nothing} step=${step ?? nothing}
        @input=${(event: Event) => set({[key]:(event.target as HTMLInputElement).value})}>`);
    return renderSettingsSection({title:'此连接的模型'}, html`
      ${this.savedConnectionModel.model_id ? renderSettingsRow({title:this.savedConnectionModel.model_id, description:'连接默认模型，在上方编辑。'}) : nothing}
      ${entries.map(item => renderSettingsRow({title:item.model_id || '', description:item.image_enabled ? '图片输入已启用' : '文字输入', control:html`
        <button class="btn btn--sm" type="button" data-testid=${`mirally-catalog-edit-${item.model_id}`} ?disabled=${this.busy||this.loading||this.modelDirty||!!this.catalogDraft} @click=${()=>{this.catalogEditing=true;this.catalogDraft=modelDraft({...this.savedConnectionModel,...item});this.deletingModel='';}}>编辑</button>
        <button class="btn btn--sm" type="button" data-testid=${`mirally-catalog-remove-${item.model_id}`} ?disabled=${this.busy||this.loading||this.modelDirty||!!this.catalogDraft} @click=${()=>{this.deletingModel=item.model_id || '';}}>移除</button>`}))}
      ${this.deletingModel ? renderSettingsRow({title:`确认移除 ${this.deletingModel}？`, description:'正在使用的模型不能移除。', control:html`
        <button class="btn btn--sm" type="button" @click=${()=>{const model_id=this.deletingModel;void this.mutate(()=>rpcProduct('model.catalog.remove',{connection_id:this.connectionId,model_id}),'模型已移除。',()=>{this.deletingModel='';});}}>确认移除</button>
        <button class="btn btn--sm" type="button" @click=${()=>{this.deletingModel='';}}>取消</button>`}) : nothing}
      ${!draft ? renderSettingsRow({title:'手动添加模型', control:html`<button class="btn btn--sm" type="button" data-testid="mirally-catalog-add" ?disabled=${this.busy||this.loading||this.modelDirty||!this.savedConnectionModel.base_url} @click=${()=>{this.catalogEditing=false;this.catalogDraft={...modelDraft(this.savedConnectionModel),model_id:'',image_enabled:false,reasoning_effort:'default'};}}>添加模型</button>`}) : html`
        <form data-testid="mirally-catalog-form" @submit=${(event:SubmitEvent)=>{event.preventDefault();const current=this.catalogDraft;if(!current)return;
          const firstModel=!this.savedConnectionModel.model_id;
          const model={model_id:current.model_id.trim(),max_tokens:Number(current.max_tokens),context_window:Number(current.context_window),temperature:Number(current.temperature),image_enabled:current.image_enabled,reasoning_effort:current.reasoning_effort,reasoning_enabled:current.reasoning_effort !== 'off'};
          void this.mutate(()=>rpcProduct('model.catalog.save',{connection_id:this.connectionId,model}),firstModel?'模型已保存，并设为此连接的默认模型。':'模型已保存。',()=>{this.catalogDraft=null;});}}>
          ${input('model_id','模型 ID')}${input('context_window','上下文窗口 token','number',128,10000000,'1')}${input('max_tokens','最大输出 token','number',128,262144,'1')}${input('temperature','温度','number',0,2,'0.1')}
          ${renderSettingsRow({title:'图片输入',control:html`<input type="checkbox" aria-label="附加模型图片输入" .checked=${live(draft.image_enabled)} ?disabled=${this.busy||this.loading} @change=${(event:Event)=>set({image_enabled:(event.target as HTMLInputElement).checked})}>`})}
          ${this.field('推理强度',html`<select class="settings-input" aria-label="附加模型推理强度" .value=${live(draft.reasoning_effort)} @change=${(event:Event)=>set({reasoning_effort:(event.target as HTMLSelectElement).value})}>
            ${['default','off','low','medium','high','max'].map(value=>html`<option value=${value} .selected=${value === draft.reasoning_effort}>${({default:'服务默认',off:'关闭',low:'Low',medium:'Medium',high:'High',max:'Max'} as Record<string,string>)[value]}</option>`)}</select>`)}
          ${renderSettingsRow({title:this.catalogEditing?'编辑模型':'添加模型',control:html`<button class="btn primary" type="submit" data-testid="mirally-catalog-save" ?disabled=${this.busy||this.loading}>保存模型</button><button class="btn btn--sm" type="button" @click=${()=>{this.catalogDraft=null;}}>取消</button>`})}
        </form>`}
    `);
  }

  private initiativePage() {
    const enabled = this.settings?.background_enabled && this.settings?.general_initiative_enabled && !this.settings?.do_not_disturb;
    return html`
      ${renderSettingsSection({title: "联系与打扰"}, html`
        ${this.toggle("background_enabled", "后台整理", "整理活动与记忆会按下方预算产生模型费用。", "mirally-settings-background")}
        ${this.toggle("general_initiative_enabled", "主动联系", "需要开启后台整理。", "mirally-settings-initiative")}
        ${this.toggle("do_not_disturb", "免打扰", "暂停主动联系，保留已设置的定时提醒。", "mirally-settings-dnd")}
        ${renderSettingsRow({title: "当前联系状态", control: renderSettingsStatus({kind: enabled ? "ok" : "muted", label: enabled ? "允许按需联系" : "主动联系已暂停"})})}
      `)}
      <form @submit=${(event: SubmitEvent) => {event.preventDefault(); if (!this.budget) return;
        const budget = Object.fromEntries(Object.entries(this.budget).map(([key, value]) => [key, Number(value)]));
        void this.mutate(() => rpcProduct("memory.configure", {patch: budget}), "后台整理预算已保存。", () => {this.budgetDirty = false;});}}>
        ${renderSettingsSection({title: "记忆整理预算"}, html`
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
        ${renderSettingsRow({title: "对话模型", control: renderSettingsValue((this.settings?.model_routes?.chat && this.settings.model_routes.chat !== 'default' ? this.settings.model_connections?.[this.settings.model_routes.chat]?.model : this.settings?.model)?.model_id || "尚未配置")})}
        ${renderSettingsRow({title: "当前运行状态", control: renderSettingsStatus({kind: this.status?.ready ? "ok" : "warn", label: this.status?.ready ? "已就绪" : "尚未确认"})})}
      `)}
      ${renderSettingsSection({title: "诊断信息"}, renderSettingsRow({title: "本机运行详情",
        stacked: true, control: html`<details data-testid="mirally-settings-diagnostics"><summary>查看诊断信息</summary><pre class="mirally-product-debug">${JSON.stringify(this.status, null, 2)}</pre></details>`}))}
      ${renderSettingsSection({title: "退出", danger: true}, renderSettingsRow({title: "退出 Mirally", description: "后台任务与对话会一并停止。",
        control: html`<button class="btn btn--sm" data-testid="mirally-settings-quit" ?disabled=${this.busy || this.loading}
          @click=${() => void this.mutate(async () => {
            await requestProduct("/api/shutdown", {method: "POST", body: "{}"});
            const native=(window as any).chrome?.webview;
            if (native && native.__mirallyBrowserOnly !== true) native.postMessage({type:'mirally-framework',action:'quit'});
          }, "Mirally 正在退出。", undefined, false)}>退出 Mirally</button>`}))}
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
