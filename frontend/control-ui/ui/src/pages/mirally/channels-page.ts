import '@awesome.me/webawesome/dist/components/qr-code/qr-code.js';
import {html, nothing} from 'lit';
import {customElement, state} from 'lit/decorators.js';
import {live} from 'lit/directives/live.js';
import {
  renderSettingsEmpty, renderSettingsPage, renderSettingsPageHeader, renderSettingsRow,
  renderSettingsSection, renderSettingsStatus, renderSettingsValue,
} from '../../components/settings-ui.ts';
import {renderSettingsWorkspace} from '../../components/settings-workspace.ts';
import {OpenClawLightDomElement} from '../../lit/openclaw-element.ts';
import {rpcProduct, type ProductData} from './api.ts';
import './product.css';
import './mobile-settings.ts';

type ConnectionState = 'unbound' | 'waiting_scan' | 'scanned' | 'need_verifycode'
  | 'awaiting_confirmation' | 'connected' | 'reconnecting' | 'disconnected'
  | 'login_required' | 'expired' | 'error';
export type WeixinStatus = {
  state: ConnectionState; enabled: boolean; bound: boolean; installation_id: string; revision: number;
  peer_id?: string; bot_id?: string; conversation_id?: string; conversation_title?: string;
  login?: {id: string; qr_content?: string; expires_at?: number | string; needs_verify_code?: boolean};
  last_error?: unknown; outbox?: {pending: number; unknown: number; failed: number};
};
type Conversation = {id: string; title?: string};
type ConversationRecord = {id?: string; conversation_id?: string; title?: string};
const STATES: Record<ConnectionState, string> = {
  unbound: '尚未连接', waiting_scan: '等待微信扫码', scanned: '已扫码，请在微信中确认',
  need_verifycode: '需要验证码', awaiting_confirmation: '等待本机确认绑定', connected: '已连接',
  reconnecting: '正在重新连接', disconnected: '已暂停连接', login_required: '需要重新登录',
  expired: '二维码已过期', error: '连接异常',
};
const LOGIN_STATES = new Set<ConnectionState>(['waiting_scan', 'scanned', 'need_verifycode']);

function expiryText(value?: number | string): string {
  if (value === undefined) return '';
  const time = typeof value === 'number' ? value : Date.parse(value);
  if (!Number.isFinite(time)) return '';
  return new Date(time).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}

/** The channel is another entrance to the same product session, never an agent owner. */
@customElement('mirally-channels-page')
export class MirallyChannelsPage extends OpenClawLightDomElement {
  @state() private status: WeixinStatus | null = null;
  @state() private remoteControl: boolean | null = null;
  @state() private conversations: Conversation[] = [];
  @state() private selection = '';
  @state() private verificationCode = '';
  @state() private loading = false;
  @state() private busy = false;
  @state() private error = '';
  @state() private notice = '';
  @state() private confirmingUnbind = false;
  private selectionDirty = false;
  private transientError = false;
  private viewEpoch = 0;
  private loadEpoch = 0;
  private pollTimer: ReturnType<typeof setTimeout> | undefined;
  private readonly hostReady = () => {void this.refresh(true);};
  private readonly visibilityChanged = () => {
    this.clearPoll();
    if (!document.hidden) void this.refresh(false);
  };

  override connectedCallback() {
    super.connectedCallback();
    this.busy = false; this.loading = false;
    window.addEventListener('mirally-host-ready', this.hostReady);
    document.addEventListener('visibilitychange', this.visibilityChanged);
    void this.refresh(true);
  }

  override disconnectedCallback() {
    ++this.viewEpoch; ++this.loadEpoch;
    this.clearPoll();
    this.verificationCode = ''; this.confirmingUnbind = false;
    window.removeEventListener('mirally-host-ready', this.hostReady);
    document.removeEventListener('visibilitychange', this.visibilityChanged);
    super.disconnectedCallback();
  }

  private clearPoll() {
    if (this.pollTimer !== undefined) clearTimeout(this.pollTimer);
    this.pollTimer = undefined;
  }

  private schedulePoll() {
    this.clearPoll();
    if (this.isConnected && !document.hidden) {
      this.pollTimer = setTimeout(() => {this.pollTimer = undefined; void this.refresh(false);}, 2000);
    }
  }

  private acceptStatus(status: WeixinStatus) {
    if (this.status?.installation_id === status.installation_id && this.status.revision > status.revision) return;
    const changedBinding = this.status?.peer_id !== status.peer_id
      || this.status?.conversation_id !== status.conversation_id
      || this.status?.installation_id !== status.installation_id;
    this.status = status;
    if (changedBinding || !this.selectionDirty) {
      this.selection = status.conversation_id || '';
      this.selectionDirty = false;
    }
    if (status.state !== 'need_verifycode' && !status.login?.needs_verify_code) this.verificationCode = '';
    if (!status.bound) this.confirmingUnbind = false;
  }

  private async refresh(includeConversations = false) {
    if (!this.isConnected || document.hidden || this.busy || this.loading) {
      this.schedulePoll(); return;
    }
    const view = this.viewEpoch, request = ++this.loadEpoch;
    if (includeConversations) this.loading = true;
    try {
      const status = await rpcProduct<WeixinStatus>('weixin.status');
      if (!this.isConnected || view !== this.viewEpoch || request !== this.loadEpoch) return;
      this.acceptStatus(status);
      const settings = await rpcProduct<{weixin_remote_control_enabled?: boolean}>('settings.get');
      if (!this.isConnected || view !== this.viewEpoch || request !== this.loadEpoch) return;
      this.remoteControl = settings.weixin_remote_control_enabled === true;
      if (includeConversations) {
        const result = await rpcProduct<{conversations: ConversationRecord[]}>('conversation.list', {archived_filter: 'active', limit: 1000});
        if (!this.isConnected || view !== this.viewEpoch || request !== this.loadEpoch) return;
        this.conversations = (result.conversations || []).flatMap(item => {
          const id = item.conversation_id || item.id;
          return id ? [{id, title: item.title}] : [];
        });
      }
      if (includeConversations || this.transientError) this.error = '';
      this.transientError = false;
    } catch {
      // Protocol errors may contain credentials or a remote response body. Never render them.
      if (this.isConnected && view === this.viewEpoch && request === this.loadEpoch) {
        if (includeConversations || !this.error || this.transientError) {
          this.error = includeConversations ? '暂时无法读取微信连接，请稍后刷新。' : '暂时无法更新连接状态，正在重试。';
          this.transientError = true;
        }
      }
    } finally {
      if (this.isConnected && view === this.viewEpoch && request === this.loadEpoch) {
        this.loading = false; this.schedulePoll();
      }
    }
  }

  private async setRemoteControl(enabled: boolean) {
    if (this.busy || this.loading || !this.isConnected) return;
    const view = this.viewEpoch;
    ++this.loadEpoch; this.clearPoll();
    this.busy = true; this.error = ''; this.notice = ''; this.transientError = false;
    try {
      const settings = await rpcProduct<{weixin_remote_control_enabled?: boolean}>('settings.update', {patch: {weixin_remote_control_enabled: enabled}});
      if (!this.isConnected || view !== this.viewEpoch) return;
      this.remoteControl = settings.weixin_remote_control_enabled === true;
      this.notice = enabled ? '已允许绑定的微信远程发起操作。' : '微信远程控制已关闭。';
    } catch {
      if (this.isConnected && view === this.viewEpoch) this.error = '远程控制设置未能保存，请刷新后重试。';
    } finally {
      if (this.isConnected && view === this.viewEpoch) {this.busy = false; this.schedulePoll();}
    }
  }

  private async mutate(method: string, params: ProductData = {}, message = '') {
    if (this.busy || this.loading || !this.isConnected) return;
    const view = this.viewEpoch;
    ++this.loadEpoch; this.clearPoll();
    this.busy = true; this.error = ''; this.notice = ''; this.transientError = false;
    try {
      const status = await rpcProduct<WeixinStatus>(method, params);
      if (!this.isConnected || view !== this.viewEpoch) return;
      this.acceptStatus(status); this.notice = message;
      if (method === 'weixin.bind' || method === 'weixin.select') this.selectionDirty = false;
      this.confirmingUnbind = false;
    } catch {
      if (this.isConnected && view === this.viewEpoch) {
        this.error = '操作未能完成，请刷新连接状态后重试。';
      }
    } finally {
      if (this.isConnected && view === this.viewEpoch) {this.busy = false; this.schedulePoll();}
    }
  }

  private bindingParams(): ProductData {
    return {expected_revision: this.status?.revision, ...(this.selection ? {conversation_id: this.selection} : {})};
  }

  private conversationPicker() {
    const missing = this.status?.conversation_id && !this.conversations.some(item => item.id === this.status?.conversation_id);
    return html`<select class="settings-input" aria-label="微信使用的会话" data-testid="mirally-weixin-conversation"
      ?disabled=${this.busy || this.loading}
      @change=${(event: Event) => {this.selection = (event.target as HTMLSelectElement).value; this.selectionDirty = true;}}>
      <option value="" .selected=${this.selection === ''}>新建一个微信会话</option>
      ${missing ? html`<option value=${this.status!.conversation_id!} .selected=${this.selection === this.status!.conversation_id}>${this.status?.conversation_title || '当前会话'}</option>` : nothing}
      ${this.conversations.map(item => html`<option value=${item.id} .selected=${this.selection === item.id}>${item.title || '未命名会话'}</option>`)}
    </select>`;
  }

  private loginSection() {
    const status = this.status!, login = status.login;
    const challenge = status.state === 'need_verifycode' || login?.needs_verify_code;
    const expiry = expiryText(login?.expires_at);
    return renderSettingsSection({title: '连接你的微信', description: '使用本人的手机微信扫码，并在微信中确认。微信授权页可能显示 OpenClaw，这是当前通道入口的名称；此处连接的是本机 Mirally。完成后还需要在这台电脑上确认绑定。'}, html`
      ${login?.qr_content && (status.state === 'waiting_scan' || status.state === 'scanned') ? renderSettingsRow({title: '微信扫码', description: expiry ? `二维码有效至 ${expiry}。请勿分享此二维码。` : '请勿分享此二维码。', stackedOnNarrow: true,
        control: html`<div class="mirally-weixin-qr" data-testid="mirally-weixin-qr"><wa-qr-code .value=${login.qr_content}
          label="使用本人微信扫描以连接 Mirally" size="200" fill="#111111" background="#ffffff"></wa-qr-code></div>`}) : nothing}
      ${challenge ? html`<form @submit=${(event: SubmitEvent) => {
        event.preventDefault(); const code = this.verificationCode.trim();
        if (!code || this.busy || this.loading) return;
        this.verificationCode = ''; void this.mutate('weixin.login.verify', {code});
      }}>${renderSettingsRow({title: '微信验证码', description: '按微信提示输入本次登录的验证码。', stackedOnNarrow: true,
        control: html`<div class="mirally-product-actions"><input class="settings-input" type="text" autocomplete="one-time-code" aria-label="微信验证码" data-testid="mirally-weixin-code"
          .value=${live(this.verificationCode)} maxlength="32" pattern="[A-Za-z0-9]+" required ?disabled=${this.busy || this.loading}
          @input=${(event: Event) => {this.verificationCode = (event.target as HTMLInputElement).value;}}>
          <button type="submit" class="btn" data-testid="mirally-weixin-verify" ?disabled=${this.busy || this.loading || !this.verificationCode.trim()}>提交验证码</button></div>`})}</form>` : nothing}
      ${renderSettingsRow({title: STATES[status.state] || '正在连接', control: html`<button class="btn btn--sm" data-testid="mirally-weixin-login-cancel" ?disabled=${this.busy || this.loading}
        @click=${() => void this.mutate('weixin.login.cancel')}>取消登录</button>`})}
    `);
  }

  private confirmationSection() {
    return renderSettingsSection({title: '确认绑定本人微信', description: '确认这是你刚才扫码的微信。绑定后，该微信可接续下方选定的 Mirally 会话。'}, html`
      ${renderSettingsRow({title: '待绑定微信', control: renderSettingsValue(this.status?.peer_id || '已完成微信确认')})}
      ${renderSettingsRow({title: '接续哪个会话', description: '选择已有会话会共享其聊天记录。也可以为微信新建会话。', control: this.conversationPicker(), stackedOnNarrow: true})}
      ${renderSettingsRow({title: '', control: html`<div class="mirally-product-actions"><button class="btn primary" data-testid="mirally-weixin-bind" ?disabled=${this.busy || this.loading}
        @click=${() => void this.mutate('weixin.bind', this.bindingParams(), '微信已绑定，可以在手机上开始对话。')}>确认绑定</button>
        <button class="btn" ?disabled=${this.busy || this.loading} @click=${() => void this.mutate('weixin.login.cancel')}>取消</button></div>`})}
    `);
  }

  private boundSection() {
    const status = this.status!;
    return html`${renderSettingsSection({title: '微信会话', description: '微信与桌面共用聊天记录。在这里选择会话，不会切换桌面正在查看的页面。'}, html`
      ${renderSettingsRow({title: '已绑定微信', control: renderSettingsValue(status.peer_id || '本人微信')})}
      ${renderSettingsRow({title: '当前会话', control: renderSettingsValue(status.conversation_title || '微信会话')})}
      ${renderSettingsRow({title: '会话与帮助', description: '发送“菜单”或“帮助”查看全部命令；/new 新建、/sessions 查看、/use 编号 切换会话。'})}
      ${renderSettingsRow({title: '回看聊天记录', description: '查看当前会话最近正文，每页最多 6 条，长消息会节选。更早记录可复制回复给出的命令继续读取。', control: html`<code>/history [更早游标]</code>`, stackedOnNarrow: true})}
      ${renderSettingsRow({title: '切换或新建会话', control: this.conversationPicker(), stackedOnNarrow: true})}
      ${renderSettingsRow({title: '', control: html`<button class="btn btn--sm" data-testid="mirally-weixin-select" ?disabled=${this.busy || this.loading || (!this.selectionDirty && !!status.conversation_id)}
        @click=${() => void this.mutate('weixin.select', this.bindingParams(), '微信会话已切换。')}>保存会话选择</button>`})}
      ${renderSettingsRow({title: '连接管理', control: html`<div class="mirally-product-actions">
        ${status.state === 'login_required' || status.state === 'expired' ? html`<button class="btn" data-testid="mirally-weixin-login" ?disabled=${this.busy || this.loading}
          @click=${() => void this.mutate('weixin.login.start')}>重新扫码登录</button>` : status.enabled ? html`<button class="btn" data-testid="mirally-weixin-disconnect" ?disabled=${this.busy || this.loading}
          @click=${() => void this.mutate('weixin.disconnect', {expected_revision: status.revision}, '连接已暂停，聊天记录保留。')}>暂停连接</button>` : html`<button class="btn" data-testid="mirally-weixin-reconnect" ?disabled=${this.busy || this.loading}
          @click=${() => void this.mutate('weixin.reconnect', {expected_revision: status.revision})}>重新连接</button>`}
      </div>`})}
    `)}
    ${renderSettingsSection({title: '解除绑定', danger: true}, renderSettingsRow({title: '移除微信连接', description: '停止收发消息并移除本机保存的微信登录凭据，Mirally 会话与聊天记录会保留。', control: this.confirmingUnbind
      ? html`<div class="mirally-product-actions" role="group" aria-label="确认解除微信绑定"><button class="btn danger" data-testid="mirally-weixin-unbind-confirm" ?disabled=${this.busy || this.loading}
          @click=${() => void this.mutate('weixin.unbind', {expected_revision: status.revision}, '已解除微信绑定，会话仍然保留。')}>确认解除绑定</button>
        <button class="btn" ?disabled=${this.busy} @click=${() => {this.confirmingUnbind = false;}}>取消</button></div>`
      : html`<button class="btn btn--sm" data-testid="mirally-weixin-unbind" ?disabled=${this.busy || this.loading} @click=${() => {this.confirmingUnbind = true;}}>解除绑定</button>`}))}`;
  }

  private workHelpSection() {
    return renderSettingsSection({title: '在微信查看与控制工作', description: '绑定后，在微信直接发送以下命令。工作编号、审批编号和运行编号会随对应回复给出；不需要开启下方的手机网页。'}, html`
      ${renderSettingsRow({title: '查看工作列表', description: '列出当前工作，也可以直接发送“我的工作怎么样了”。', control: html`<code>/works</code>`, stackedOnNarrow: true})}
      ${renderSettingsRow({title: '查看具体工作', description: '读取最新状态、运行与待确认动作；不会切换微信当前会话。', control: html`<code>/work 工作编号</code>`, stackedOnNarrow: true})}
      ${renderSettingsRow({title: '继续这项工作的对话', description: '切换微信后续消息到该工作的主会话，并停止微信此前发起的回复；后台运行继续。', control: html`<code>/work-use 工作编号</code>`, stackedOnNarrow: true})}
      ${renderSettingsRow({title: '审阅待确认动作', description: '先查看具体工作中的审批编号，再核对脚本路径、指纹、参数、输出和有效期，获取一次性确认码。', control: html`<code>/work-review 完整审批编号</code>`, stackedOnNarrow: true})}
      ${renderSettingsRow({title: '批准并启动这一次运行', description: '使用刚审阅动作的短期确认码。确认码绑定当前微信入口与会话，不代表长期授权。', control: html`<code>/work-approve 确认码</code>`, stackedOnNarrow: true})}
      ${renderSettingsRow({title: '停止真实后台运行', description: '请求停止 Mirally 启动的指定运行，实际停止由执行器核实；已发生的效果不会撤销。仅关注的外部程序没有此控制权限。', control: html`<code>/work-cancel 完整运行编号</code>`, stackedOnNarrow: true})}
      ${renderSettingsRow({title: '查看运行日志', description: '从工作详情复制完整运行编号；按回复给出的命令继续读取后续日志。', control: html`<code>/work-logs 完整运行编号 [字节偏移]</code>`, stackedOnNarrow: true})}
      ${renderSettingsRow({title: '微信工作通知', description: '不带参数查看；on 开启、off 关闭。只调整微信工作通知，不改消息页通知，也不停止工作。', control: html`<code>/work-notify [on|off]</code>`, stackedOnNarrow: true})}
      ${renderSettingsRow({title: '停止当前会话', description: '停止当前会话回复及其受管理的会话后台命令；工作运行需用 /work-cancel 停止。', control: html`<code>/stop</code>`, stackedOnNarrow: true})}
      ${renderSettingsRow({title: '创建与准备', description: '发送 /work-new 目标 建立工作；/work-run 工作编号 后附脚本参数 JSON 可准备一次运行。准备不会直接执行，仍需审阅并确认。发送 /help 查看完整用法。'})}
    `);
  }

  override render() {
    const status = this.status, state = status?.state;
    return html`${renderSettingsPageHeader({title: '消息渠道', subtitle: '在微信接续电脑上的工作与对话；手机网页可按需扩展。', actions: html`
      <button class="btn btn--sm" data-testid="mirally-weixin-refresh" ?disabled=${this.loading || this.busy} @click=${() => void this.refresh(true)}>${this.loading ? '正在刷新…' : '刷新'}</button>`})}
      ${renderSettingsWorkspace(renderSettingsPage(html`<div class="mirally-settings-content" data-testid="mirally-weixin-page" aria-busy=${this.loading || this.busy ? 'true' : 'false'}>
        ${this.error ? html`<div class="callout danger" role="alert" data-testid="mirally-weixin-error">${this.error}</div>` : nothing}
        ${this.notice ? html`<div class="callout" role="status" data-testid="mirally-weixin-notice">${this.notice}</div>` : nothing}
        ${renderSettingsSection({title: '微信', description: '支持本人文字私聊、工作查询，以及明确确认后的脚本运行。普通聊天不会自动启动后台脚本。'}, html`
          ${renderSettingsRow({title: '连接状态', control: renderSettingsStatus({kind: state === 'connected' ? 'ok' : state === 'error' || state === 'login_required' ? 'warn' : 'muted', label: state ? STATES[state] || '状态待确认' : '正在读取…'})})}
          ${renderSettingsRow({title: '使用方式', description: '无需自建云服务器或端口映射。完成微信绑定后，让这台电脑保持联网并运行 Mirally，即可在微信使用。私人 HTTPS 和手机网页配对都不是微信的使用前置。微信发送窗口失效时，请再发一条消息后继续。'})}
          ${status?.last_error ? renderSettingsRow({title: '连接提示', description: state === 'login_required' ? '微信授权已失效，请重新扫码。' : '连接暂时异常，请检查网络或刷新后重新连接。'}) : nothing}
        `)}
        ${!status ? renderSettingsEmpty(this.loading ? '正在读取微信设置…' : '尚未读取到连接状态，请刷新后重试。')
          : LOGIN_STATES.has(status.state) ? this.loginSection()
          : status.state === 'awaiting_confirmation' ? this.confirmationSection()
          : status.bound ? this.boundSection()
          : renderSettingsSection({title: '开始连接'}, renderSettingsRow({title: '连接本人微信', description: status.state === 'expired' ? '二维码已过期，请重新生成。' : '生成二维码后，使用微信扫码登录。',
            control: html`<button class="btn primary" data-testid="mirally-weixin-login" ?disabled=${this.busy || this.loading}
              @click=${() => void this.mutate('weixin.login.start')}>${status.state === 'expired' ? '重新生成二维码' : '连接微信'}</button>`}))}
        ${renderSettingsSection({title: '微信远程控制', description: '允许已绑定的微信在共享会话中请求命令、独立浏览器和屏幕观察，每次需确认具体操作。解除绑定或重新扫码后需要重新开启。'}, renderSettingsRow({title: '允许微信远程控制', description: status?.bound ? '关闭后仍可对话和查看会话后台命令；已启动的程序不会因此自动停止。' : '先绑定微信，才能开启远程控制。', control: html`<input type="checkbox" aria-label="允许微信远程控制" data-testid="mirally-weixin-remote-control" .checked=${live(this.remoteControl === true)} ?disabled=${this.busy || this.loading || this.remoteControl === null || !status?.bound} @change=${(event: Event) => void this.setRemoteControl((event.target as HTMLInputElement).checked)}>`}))}
        ${status?.outbox && (status.outbox.pending || status.outbox.unknown || status.outbox.failed) ? renderSettingsSection({title: '消息投递', description: '微信接受发送请求，不代表对方已经阅读。'}, html`
          ${renderSettingsRow({title: '待发送', control: renderSettingsValue(String(status.outbox.pending || 0))})}
          ${renderSettingsRow({title: '送达待确认', description: '无法确定微信是否已收到，不会自动重复发送。', control: renderSettingsValue(String(status.outbox.unknown || 0))})}
          ${renderSettingsRow({title: '发送失败', description: '可以在对应 Mirally 会话查看回复。', control: renderSettingsValue(String(status.outbox.failed || 0))})}
        `) : nothing}
        ${this.workHelpSection()}
        <mirally-mobile-settings></mirally-mobile-settings>
      </div>`))}`;
  }
}
