import { html, nothing, type TemplateResult } from "lit";
import { state } from "lit/decorators.js";
import { live } from "lit/directives/live.js";
import {
  renderSettingsEmpty, renderSettingsPage, renderSettingsPageHeader,
  renderSettingsRow, renderSettingsSection, renderSettingsStatus,
} from "../../components/settings-ui.ts";
import { renderSettingsWorkspace } from "../../components/settings-workspace.ts";
import { OpenClawLightDomElement } from "../../lit/openclaw-element.ts";
import { errorMessage, rpcProduct } from "./api.ts";
import {
  buildExtension, editableExtension, extensionStatus, extensionType, newExtensionDraft,
  requireList, type ExtensionDraft, type RecordData,
} from "./extensions-model.ts";
import "./product.css";

type CatalogKind = "resources" | "prompts" | "templates";
type ServerCatalog = { server: string; items: RecordData[]; error?: string; nextCursor?: string };
const catalogMethod = (kind: CatalogKind) => kind === "templates" ? "mcp.resources.templates" : `mcp.${kind}.list`;
const catalogKey = (kind: CatalogKind) => kind === "templates" ? "resourceTemplates" : kind;

/** Product extension management reuses the existing settings surface and native owner. */
export class MirallyExtensionsPage extends OpenClawLightDomElement {
  @state() private extensions: RecordData[] | null = null;
  @state() private skills: RecordData[] | null = null;
  @state() private tools: RecordData[] | null = null;
  @state() private catalogs: Partial<Record<CatalogKind, ServerCatalog[]>> = {};
  @state() private errors: Record<string, string> = {};
  @state() private loading = false;
  @state() private changing = false;
  @state() private notice = "";
  @state() private formOpen = false;
  @state() private draft = newExtensionDraft();
  @state() private reviewed = false;
  @state() private disableId = "";
  @state() private disableReviewed = false;
  @state() private detail: {title: string; description?: string; content?: string} | null = null;
  @state() private detailBusy = false;
  @state() private detailError = "";
  @state() private prompt: {server: string; definition: RecordData; args: Record<string, string>} | null = null;
  @state() private catalogBusy = "";
  private generation = 0;
  private detailGeneration = 0;
  private readonly onHostReady = () => { void this.load(); };

  override connectedCallback() {
    super.connectedCallback();
    window.addEventListener("mirally-host-ready", this.onHostReady);
    void this.load();
  }
  override disconnectedCallback() {
    ++this.generation;
    ++this.detailGeneration;
    window.removeEventListener("mirally-host-ready", this.onHostReady);
    super.disconnectedCallback();
  }

  private async load() {
    const generation = ++this.generation;
    this.loading = true;
    const tasks: [string, () => Promise<unknown>, (value: any) => void][] = [
      ["extensions", () => rpcProduct("extensions.list"), value => { this.extensions = requireList(value, "extensions"); }],
      ["skills", () => rpcProduct("skills.list"), value => { this.skills = requireList(value, "skills"); }],
      ["tools", () => rpcProduct("mcp.tools.list"), value => { this.tools = requireList(value, "tools"); }],
      ...(["resources", "prompts", "templates"] as CatalogKind[]).map(kind => [
        kind, () => rpcProduct(catalogMethod(kind)), value => {
          this.catalogs = {...this.catalogs, [kind]: requireList(value, "servers").map(server => this.serverCatalog(server, kind))};
        },
      ] as [string, () => Promise<unknown>, (value: any) => void]),
    ];
    await Promise.allSettled(tasks.map(async ([key, request, accept]) => {
      try {
        const value = await request();
        if (!this.isConnected || generation !== this.generation) return;
        accept(value);
        const next = {...this.errors}; delete next[key]; this.errors = next;
      } catch (error) {
        if (this.isConnected && generation === this.generation) this.errors = {...this.errors, [key]: errorMessage(error)};
      }
    }));
    if (this.isConnected && generation === this.generation) this.loading = false;
  }

  private serverCatalog(value: RecordData, kind: CatalogKind): ServerCatalog {
    return {server: String(value.server ?? value.id ?? ""),
      items: value.error ? [] : requireList(value, catalogKey(kind)),
      ...(value.error ? {error: String(value.error)} : {}),
      ...(value.nextCursor ? {nextCursor: String(value.nextCursor)} : {})};
  }

  private async moreCatalog(kind: CatalogKind, current: ServerCatalog) {
    if (!current.nextCursor || this.catalogBusy) return;
    this.catalogBusy = `${kind}:${current.server}`;
    const generation = this.generation;
    try {
      const value = await rpcProduct<RecordData>(catalogMethod(kind), {server: current.server, cursor: current.nextCursor});
      const next = this.serverCatalog({...value, server: current.server}, kind);
      if (!this.isConnected || generation !== this.generation) return;
      this.catalogs = {...this.catalogs, [kind]: this.catalogs[kind]?.map(server => server === current
        ? {...next, items: [...current.items, ...next.items]} : server)};
    } catch (error) {
      if (this.isConnected && generation === this.generation) this.errors = {...this.errors, [kind]: errorMessage(error)};
    } finally { if (this.isConnected) this.catalogBusy = ""; }
  }

  private updateDraft<K extends keyof ExtensionDraft>(key: K, value: ExtensionDraft[K]) {
    this.draft = {...this.draft, [key]: value};
    this.reviewed = false;
  }

  private openNew() {
    this.draft = newExtensionDraft();
    this.reviewed = false;
    this.formOpen = true;
    this.notice = "";
  }

  private edit(extension: RecordData, enable = false) {
    const spec = editableExtension(extension);
    this.draft = {...newExtensionDraft(), type: "advanced", advanced: JSON.stringify(enable ? {...spec, enabled: true} : spec, null, 2)};
    this.reviewed = false;
    this.formOpen = true;
    this.notice = "";
  }

  private async configure(event: Event) {
    event.preventDefault();
    if (this.changing) return;
    this.errors = {...this.errors, configure: ""};
    try {
      if (!this.reviewed) throw new Error("请先确认已检查扩展来源与配置。");
      const extension = buildExtension(this.draft);
      this.changing = true;
      const result = await rpcProduct<RecordData>("extensions.configure", {extension});
      if (!this.isConnected) return;
      this.extensions = requireList(result, "extensions");
      const saved = this.extensions.find(item => item.id === extension.id);
      this.draft = newExtensionDraft();
      this.reviewed = false;
      this.formOpen = false;
      ++this.detailGeneration;
      this.detailBusy = false;
      this.detailError = "";
      this.detail = null;
      this.prompt = null;
      this.notice = saved?.status === "active" ? "扩展已启用。会话中的能力可能已更新，请在下次发送前检查当前任务。"
        : saved?.status === "disabled" ? "配置已保存，扩展保持停用。"
        : "配置已保存，但扩展未成功启用；请查看列表中的状态和错误。";
      await this.load();
    } catch (error) {
      if (this.isConnected) this.errors = {...this.errors, configure: `${errorMessage(error)} 如连接在提交后中断，请先刷新列表核对保存状态。`};
    }
    finally { if (this.isConnected) this.changing = false; }
  }

  private async disable() {
    if (!this.disableId || !this.disableReviewed || this.changing) return;
    const id = this.disableId;
    this.changing = true;
    try {
      const result = await rpcProduct<RecordData>("extensions.disable", {id});
      if (!this.isConnected) return;
      this.extensions = requireList(result, "extensions");
      this.disableId = "";
      this.disableReviewed = false;
      ++this.detailGeneration;
      this.detailBusy = false;
      this.detailError = "";
      this.detail = null;
      this.prompt = null;
      this.notice = "扩展已停用，相关 Skill、工具与连接已撤回。会话可用能力可能已更新。";
      const next = {...this.errors}; delete next.change; this.errors = next;
      await this.load();
    } catch (error) { if (this.isConnected) this.errors = {...this.errors, change: errorMessage(error)}; }
    finally { if (this.isConnected) this.changing = false; }
  }

  private async readDetail(title: string, method: string, params: RecordData, description?: string) {
    const generation = ++this.detailGeneration;
    this.detail = {title, description};
    this.detailError = "";
    this.detailBusy = true;
    try {
      const result = await rpcProduct<RecordData>(method, params);
      if (!this.isConnected || generation !== this.detailGeneration) return;
      let content: string;
      if (method === "skills.read") {
        if (typeof result.content !== "string") throw new Error("Skill 未返回有效的说明内容。");
        content = result.content;
      } else if (method === "mcp.resources.read") {
        content = requireList(result, "contents").map(part => typeof part.text === "string" ? part.text
          : `二进制资源：${part.mimeType || "未知类型"}（此处不解码或执行）`).join("\n\n");
      } else {
        content = requireList(result, "messages").map(message => {
          const part = message.content;
          return `${message.role === "assistant" ? "助手" : "用户"}：\n${typeof part?.text === "string" ? part.text
            : `非文本内容：${part?.type || "未知类型"}`}`;
        }).join("\n\n");
      }
      this.detail = {title, description, content: content || "返回内容为空。"};
    } catch (error) {
      if (this.isConnected && generation === this.detailGeneration) this.detailError = errorMessage(error);
    } finally { if (this.isConnected && generation === this.detailGeneration) this.detailBusy = false; }
  }

  private input(key: keyof ExtensionDraft, label: string, options: {placeholder?: string; multiline?: boolean; type?: string} = {}) {
    return html`<label class="mirally-product-field"><span>${label}</span>${options.multiline
      ? html`<textarea data-extension-field=${key} .value=${live(String(this.draft[key]))} placeholder=${options.placeholder ?? ""}
          ?disabled=${this.changing} @input=${(event: Event) => this.updateDraft(key, (event.target as HTMLTextAreaElement).value as never)}></textarea>`
      : html`<input data-extension-field=${key} type=${options.type ?? "text"} .value=${live(String(this.draft[key]))}
          placeholder=${options.placeholder ?? ""} autocomplete="off" spellcheck="false" ?disabled=${this.changing}
          @input=${(event: Event) => this.updateDraft(key, (event.target as HTMLInputElement).value as never)}>`}</label>`;
  }

  private renderForm() {
    if (!this.formOpen) return nothing;
    return renderSettingsSection({title: "接入扩展", description: "连接已有的 Skill、MCP 服务或可信本地插件。相对路径以 Mirally 仓库目录为基准；这里不会安装软件包。"}, html`
      <form class="mirally-product-form" data-testid="mirally-extension-form" @submit=${(event: Event) => void this.configure(event)}>
        <label class="mirally-product-field"><span>扩展类型</span><select data-extension-field="type" .value=${live(this.draft.type)}
          ?disabled=${this.changing} @change=${(event: Event) => this.updateDraft("type", (event.target as HTMLSelectElement).value as ExtensionDraft["type"])}>
          <option value="skill">Skill 目录</option><option value="mcp">MCP 服务</option><option value="plugin">可信本地插件</option><option value="advanced">完整配置 JSON</option>
        </select></label>
        ${this.draft.type === "advanced" ? html`
          ${this.input("advanced", "完整扩展配置", {multiline: true, placeholder: '{"id":"my-extension","type":"skill","path":"./skills","enabled":true}'})}
          <p class="mirally-product-meta">编辑时保留原标识可更新已有扩展。配置中的已保存凭据以掩码显示，保留掩码会沿用原值。启用、依赖、脚本白名单及组合扩展均由内核校验。</p>` : html`
          ${this.input("id", "扩展标识", {placeholder: "my-extension"})}
          ${this.draft.type !== "mcp" ? this.input("path", this.draft.type === "skill" ? "Skill 根目录" : "插件模块路径", {placeholder: this.draft.type === "skill" ? "./skills" : "./plugins/my-plugin.mjs"}) : html`
            <div class="mirally-product-grid">${this.input("server", "MCP 服务名", {placeholder: "my_server"})}
              <label class="mirally-product-field"><span>连接方式</span><select data-extension-field="transport" .value=${live(this.draft.transport)}
                ?disabled=${this.changing} @change=${(event: Event) => this.updateDraft("transport", (event.target as HTMLSelectElement).value as ExtensionDraft["transport"])}>
                <option value="stdio">本地进程（stdio）</option><option value="streamable-http">HTTP（Streamable HTTP）</option>
              </select></label></div>
            ${this.draft.transport === "stdio" ? html`
              ${this.input("command", "启动命令", {placeholder: "python 或 node 或已安装的可执行程序"})}
              ${this.input("args", "启动参数（每行一个，保留参数中的空格）", {multiline: true})}
              ${this.input("cwd", "工作目录（选填，默认仓库目录）")}` : this.input("url", "MCP 服务地址", {placeholder: "https://example.com/mcp", type: "url"})}
            <details><summary>${this.draft.transport === "stdio" ? "环境变量（选填）" : "请求头与凭据（选填）"}</summary>
              ${this.input("secrets", this.draft.transport === "stdio" ? "环境变量 JSON" : "请求头 JSON", {multiline: true, placeholder: "{}"})}
              <p class="mirally-product-meta">提交后由本机配置保存，后续列表仅显示掩码。</p>
            </details>
            <label><input type="checkbox" .checked=${live(this.draft.readOnly)} ?disabled=${this.changing}
              @change=${(event: Event) => this.updateDraft("readOnly", (event.target as HTMLInputElement).checked)}> 此服务声明只读（影响模型调用授权；请据服务实际能力设置）</label>`}
          <p class="mirally-product-meta">${this.draft.type === "skill" ? "根目录包含各 Skill 的 SKILL.md。读取说明不会自动授权执行脚本；脚本须单独配置白名单并确认。"
            : this.draft.type === "plugin" ? "支持可信 DSH 原生插件与 Mirally API v1。插件会在宿主内执行；更新模块代码后需要重启 Mirally。"
            : "启用会启动本地服务或连接指定地址，并读取服务公开的能力。工具调用仍使用 Mirally 会话中的授权规则。"}</p>`}
        <p class="mirally-product-meta">应用或停用扩展会停止正在进行的回复，并为后续对话更新能力。</p>
        <label><input data-testid="extension-review" type="checkbox" .checked=${live(this.reviewed)} ?disabled=${this.changing}
          @change=${(event: Event) => { this.reviewed = (event.target as HTMLInputElement).checked; }}>
          我已检查来源和配置，确认接入；同标识会替换已有配置，并可能更新当前会话的能力。</label>
        ${this.errors.configure ? html`<p role="alert" class="mirally-product-notice" data-error>${this.errors.configure}</p>` : nothing}
        <div class="mirally-product-actions"><button class="btn primary" type="submit" ?disabled=${this.changing || !this.reviewed}>${this.changing ? "正在应用…" : "保存并应用"}</button>
          <button class="btn" type="button" ?disabled=${this.changing} @click=${() => { this.formOpen = false; this.draft = newExtensionDraft(); this.reviewed = false; }}>取消</button></div>
      </form>`);
  }

  private renderExtensions() {
    return renderSettingsSection({title: "已配置扩展", count: this.errors.extensions ? undefined : this.extensions?.length,
      description: "状态来自当前 Mirally 内核。停用会撤回对应能力，可能影响正在进行的会话。"}, html`
      ${this.renderError("extensions")}
      ${this.extensions === null ? renderSettingsEmpty(this.loading ? "正在读取扩展…" : "扩展列表尚未载入。")
        : this.extensions.length === 0 ? renderSettingsEmpty("尚未配置扩展。可接入本地 Skill 目录、MCP 服务或可信插件。")
        : this.extensions.map(extension => html`<div class="mirally-product-item" data-extension-id=${extension.id}>
          ${renderSettingsRow({title: extension.name || extension.id, description: `${extensionType(extension.type)} · ${extension.id} · ${extension.version || "未声明版本"}`,
            control: renderSettingsStatus({kind: extension.status === "active" ? "ok" : extension.status === "error" ? "danger" : "muted", label: extensionStatus(extension.status)})})}
          ${extension.description ? html`<p>${extension.description}</p>` : nothing}
          ${extension.error ? html`<p role="alert" class="mirally-product-notice" data-error>${extension.error}</p>` : nothing}
          ${extension.path ? html`<p class="mirally-product-meta">${extension.path}</p>` : nothing}
          ${extension.capabilities ? html`<p class="mirally-product-meta">Skill 目录 ${extension.capabilities.skills} · MCP 服务 ${extension.capabilities.mcp} · 脚本 ${extension.capabilities.scripts}${extension.capabilities.plugin ? " · 原生插件" : ""}</p>` : nothing}
          <div class="mirally-product-actions"><button class="btn btn--sm" ?disabled=${this.changing} @click=${() => this.edit(extension)}>编辑配置</button>
            ${extension.status === "disabled" ? html`<button class="btn btn--sm" ?disabled=${this.changing} @click=${() => this.edit(extension, true)}>启用…</button>`
              : html`<button class="btn btn--sm" ?disabled=${this.changing}
                @click=${() => { this.disableId = extension.id; this.disableReviewed = false; }}>停用</button>`}</div>
          ${this.disableId === extension.id ? html`<div class="mirally-product-form" role="group" aria-label="确认停用扩展">
            <label><input type="checkbox" .checked=${live(this.disableReviewed)} ?disabled=${this.changing}
              @change=${(event: Event) => { this.disableReviewed = (event.target as HTMLInputElement).checked; }}> 确认停用 ${extension.id}，撤回相关工具、说明和连接；正在运行的相关操作可能中止。</label>
            <div class="mirally-product-actions"><button class="btn" ?disabled=${this.changing || !this.disableReviewed} @click=${() => void this.disable()}>确认停用</button>
              <button class="btn" ?disabled=${this.changing} @click=${() => { this.disableId = ""; }}>取消</button></div></div>` : nothing}
        </div>`)}
      ${this.renderError("change")}`);
  }

  private renderError(key: string) {
    return this.errors[key] ? html`<p class="mirally-product-notice" role="alert" data-error>${this.errors[key]}${key !== "configure" && key !== "change" ? " 列表可能尚未载入或已过期，请刷新重试。" : ""}</p>` : nothing;
  }

  private renderSkills() {
    return renderSettingsSection({title: "可用 Skill", count: this.errors.skills ? undefined : this.skills?.length,
      description: "查看模型当前可发现的说明与资源来源。Skill 说明不会自动执行。"}, html`${this.renderError("skills")}
      ${this.skills === null ? renderSettingsEmpty(this.loading ? "正在读取 Skill…" : "Skill 目录尚未载入。")
        : this.skills.length === 0 ? renderSettingsEmpty("当前没有可用 Skill。")
        : this.skills.map(skill => html`<div class="mirally-product-item" data-skill-name=${skill.name}>
          ${renderSettingsRow({title: skill.name, description: skill.description,
            control: html`<button class="btn btn--sm" @click=${() => { this.prompt = null; void this.readDetail(skill.name, "skills.read", {name: skill.name}, skill.description); }}>查看说明</button>`})}
          <p class="mirally-product-meta">来源：${skill.provider || skill.source || "未声明"}${skill.path ? ` · ${skill.path}` : ""}</p>
        </div>`)}`);
  }

  private renderTools() {
    return renderSettingsSection({title: "MCP 工具", count: this.errors.tools ? undefined : this.tools?.length,
      description: "已启用服务实际注册的模型工具。需要使用时在会话中提出请求，执行沿用 Mirally 的授权与进展记录。"}, html`${this.renderError("tools")}
      ${this.tools === null ? renderSettingsEmpty(this.loading ? "正在读取工具…" : "工具目录尚未载入。")
        : this.tools.length === 0 ? renderSettingsEmpty("当前没有已注册的 MCP 工具。")
        : this.tools.map(tool => html`<div class="mirally-product-item"><h3>${tool.name}</h3><p>${tool.description || "服务未提供描述。"}</p>
          ${tool.parameters || tool.inputSchema ? html`<details><summary>参数定义</summary><pre class="mirally-product-debug">${JSON.stringify(tool.parameters ?? tool.inputSchema, null, 2)}</pre></details>` : nothing}
        </div>`)}`);
  }

  private renderCatalog(kind: CatalogKind, title: string, description: string) {
    const servers = this.catalogs[kind];
    return renderSettingsSection({title, description}, html`${this.renderError(kind)}
      ${!servers ? renderSettingsEmpty(this.loading ? "正在读取服务目录…" : "服务目录尚未载入。")
        : servers.length === 0 ? renderSettingsEmpty("当前没有已连接的 MCP 服务。")
        : servers.map(server => html`<div class="mirally-product-item"><h3>${server.server}</h3>
          ${server.error ? html`<p role="alert" class="mirally-product-notice" data-error>${server.error}</p>`
            : server.items.length === 0 ? html`<p class="mirally-product-meta">此服务未返回${title}条目。</p>`
            : server.items.map(item => renderSettingsRow({title: item.title || item.name || item.uri || item.uriTemplate,
              description: html`${item.description ?? ""}${item.uri || item.uriTemplate ? html`<span class="mirally-product-body">${item.uri || item.uriTemplate}</span>` : nothing}`,
              control: kind === "resources" ? html`<button class="btn btn--sm" @click=${() => { this.prompt = null; void this.readDetail(item.title || item.name || item.uri, "mcp.resources.read", {server: server.server, uri: item.uri}, `来源：${server.server}`); }}>读取</button>`
                : kind === "prompts" ? html`<button class="btn btn--sm" @click=${() => { ++this.detailGeneration; this.detailBusy = false; this.detailError = ""; this.detail = null; this.prompt = {server: server.server, definition: item, args: {}}; }}>查看模板</button>` : nothing}))}
          ${server.nextCursor ? html`<button class="btn btn--sm" ?disabled=${!!this.catalogBusy || this.loading}
            @click=${() => void this.moreCatalog(kind, server)}>${this.catalogBusy === `${kind}:${server.server}` ? "正在读取…" : "加载更多"}</button>` : nothing}
        </div>`)}`);
  }

  private renderPrompt() {
    if (!this.prompt) return nothing;
    const prompt = this.prompt;
    const args = Array.isArray(prompt.definition.arguments) ? prompt.definition.arguments : [];
    return renderSettingsSection({title: `提示模板：${prompt.definition.name}`, description: `来源：${prompt.server}。读取后仅展示内容，不会自动加入会话或执行。`}, html`
      <form class="mirally-product-form" @submit=${(event: Event) => {
        event.preventDefault();
        void this.readDetail(prompt.definition.name, "mcp.prompts.get", {server: prompt.server, name: prompt.definition.name, arguments: prompt.args}, `来源：${prompt.server}`);
      }}>
        ${args.map((arg: RecordData) => html`<label class="mirally-product-field"><span>${arg.name}${arg.required ? "（必填）" : ""}</span>
          ${arg.description ? html`<span class="mirally-product-meta">${arg.description}</span>` : nothing}
          <input name=${arg.name} ?required=${arg.required === true} .value=${live(prompt.args[arg.name] || "")}
            @input=${(event: Event) => { this.prompt = {...prompt, args: {...prompt.args, [arg.name]: (event.target as HTMLInputElement).value}}; }}></label>`)}
        <div class="mirally-product-actions"><button class="btn" type="submit" ?disabled=${this.detailBusy}>读取模板</button>
          <button class="btn" type="button" @click=${() => { this.prompt = null; }}>关闭</button></div>
      </form>`);
  }

  private renderDetail() {
    if (!this.detail) return nothing;
    return renderSettingsSection({title: this.detail.title, description: this.detail.description,
      actions: html`<button class="btn btn--sm" @click=${() => { ++this.detailGeneration; this.detail = null; this.detailBusy = false; }}>关闭详情</button>`}, html`
      <div class="mirally-product-item" data-testid="mirally-extension-detail">
        ${this.detailBusy ? html`<p role="status">正在读取…</p>` : nothing}
        ${this.detailError ? html`<p role="alert" class="mirally-product-notice" data-error>${this.detailError}</p>` : nothing}
        ${this.detail.content !== undefined ? html`<pre class="mirally-product-body">${this.detail.content}</pre>` : nothing}
      </div>`);
  }

  override render(): TemplateResult {
    return html`
      ${renderSettingsPageHeader({title: "扩展", subtitle: "为 Mirally 接入说明、服务与可信本地能力。",
        actions: html`<button class="btn" ?disabled=${this.loading || this.changing} @click=${() => void this.load()}>${this.loading ? "正在刷新…" : "刷新"}</button>
          <button class="btn primary" ?disabled=${this.changing} @click=${() => this.openNew()}>接入扩展</button>`})}
      ${renderSettingsWorkspace(renderSettingsPage(html`
      ${this.notice ? html`<p class="mirally-product-notice" role="status">${this.notice}</p>` : nothing}
      ${this.renderForm()}${this.renderExtensions()}${this.renderSkills()}
      ${this.renderPrompt()}${this.renderDetail()}${this.renderTools()}
      ${this.renderCatalog("resources", "资源", "从已连接的 MCP 服务读取资源，内容保留服务来源。")}
      ${this.renderCatalog("prompts", "提示模板", "查看服务公开的提示模板，可在填入参数后读取。")}
      ${this.renderCatalog("templates", "资源模板", "服务声明的资源 URI 模板；这里展示目录，不自动展开或抓取。")}
    `), {id: "mirally-extensions", ariaLabel: "Mirally 扩展"})}`;
  }
}

if (!customElements.get("mirally-extensions-page")) customElements.define("mirally-extensions-page", MirallyExtensionsPage);
