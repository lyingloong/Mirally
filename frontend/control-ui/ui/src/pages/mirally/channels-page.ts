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
      ${renderSettingsRow({title: '在微信中操作', description: '发送 /help 查看帮助，/new 新建会话，/sessions 查看会话，/stop 停止当前回复。'})}
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

  override render() {
    const status = this.status, state = status?.state;
    return html`${renderSettingsPageHeader({title: '消息渠道', subtitle: '通过微信，与这台电脑上的 Mirally 继续交流。', actions: html`
      <button class="btn btn--sm" data-testid="mirally-weixin-refresh" ?disabled=${this.loading || this.busy} @click=${() => void this.refresh(true)}>${this.loading ? '正在刷新…' : '刷新'}</button>`})}
      ${renderSettingsWorkspace(renderSettingsPage(html`<div class="mirally-settings-content" data-testid="mirally-weixin-page" aria-busy=${this.loading || this.busy ? 'true' : 'false'}>
        ${this.error ? html`<div class="callout danger" role="alert" data-testid="mirally-weixin-error">${this.error}</div>` : nothing}
        ${this.notice ? html`<div class="callout" role="status" data-testid="mirally-weixin-notice">${this.notice}</div>` : nothing}
        ${renderSettingsSection({title: '微信', description: '目前支持本人文字私聊；可读取获准信息，暂不执行命令或控制桌面。'}, html`
          ${renderSettingsRow({title: '连接状态', control: renderSettingsStatus({kind: state === 'connected' ? 'ok' : state === 'error' || state === 'login_required' ? 'warn' : 'muted', label: state ? STATES[state] || '状态待确认' : '正在读取…'})})}
          ${renderSettingsRow({title: '使用方式', description: '需要这台电脑保持 Mirally 运行。微信发送窗口失效时，请再发一条消息后继续。'})}
          ${status?.last_error ? renderSettingsRow({title: '连接提示', description: state === 'login_required' ? '微信授权已失效，请重新扫码。' : '连接暂时异常，请检查网络或刷新后重新连接。'}) : nothing}
        `)}
        ${!status ? renderSettingsEmpty(this.loading ? '正在读取微信设置…' : '尚未读取到连接状态，请刷新后重试。')
          : LOGIN_STATES.has(status.state) ? this.loginSection()
          : status.state === 'awaiting_confirmation' ? this.confirmationSection()
          : status.bound ? this.boundSection()
          : renderSettingsSection({title: '开始连接'}, renderSettingsRow({title: '连接本人微信', description: status.state === 'expired' ? '二维码已过期，请重新生成。' : '生成二维码后，使用微信扫码登录。',
            control: html`<button class="btn primary" data-testid="mirally-weixin-login" ?disabled=${this.busy || this.loading}
              @click=${() => void this.mutate('weixin.login.start')}>${status.state === 'expired' ? '重新生成二维码' : '连接微信'}</button>`}))}
        ${status?.outbox && (status.outbox.pending || status.outbox.unknown || status.outbox.failed) ? renderSettingsSection({title: '消息投递', description: '微信接受发送请求，不代表对方已经阅读。'}, html`
          ${renderSettingsRow({title: '待发送', control: renderSettingsValue(String(status.outbox.pending || 0))})}
          ${renderSettingsRow({title: '送达待确认', description: '无法确定微信是否已收到，不会自动重复发送。', control: renderSettingsValue(String(status.outbox.unknown || 0))})}
          ${renderSettingsRow({title: '发送失败', description: '可以在对应 Mirally 会话查看回复。', control: renderSettingsValue(String(status.outbox.failed || 0))})}
        `) : nothing}
      </div>`))}`;
  }
}
