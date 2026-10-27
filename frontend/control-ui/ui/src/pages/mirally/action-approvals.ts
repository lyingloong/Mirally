import {html, nothing, type PropertyValues} from 'lit';
import {customElement, property, state} from 'lit/decorators.js';
import {OpenClawLightDomElement} from '../../lit/openclaw-element.ts';
import {rpcProduct, errorMessage} from './api.ts';
import './product.css';

type Approval = {id:string; title:string; detail:string; conversation_id:string};

/** Decisions are read from the product owner, scoped to this conversation. */
@customElement('mirally-action-approvals')
export class MirallyActionApprovals extends OpenClawLightDomElement {
  @property() sessionKey = '';
  @property({type:Boolean}) presented = true;
  @state() private approvals: Approval[] = [];
  @state() private busy = false;
  @state() private error = '';
  @state() private sessionAutoApprove: boolean | null = null;
  @state() private defaultTrusted = false;
  @state() private permissionBusy = false;
  @state() private permissionError = '';
  private epoch = 0;
  private permissionVersion = 0;
  private timer?: ReturnType<typeof setTimeout>;
  private get conversationId() { return this.sessionKey.replace(/^agent:main:/, ''); }
  private readonly changed = (event: Event) => {
    const detail = (event as CustomEvent).detail;
    if (detail?.action === 'approval-changed' && detail.conversation_id === this.conversationId) void this.refresh();
  };
  override connectedCallback() { super.connectedCallback(); window.addEventListener('mirally-product-event', this.changed); }
  override disconnectedCallback() { ++this.epoch; ++this.permissionVersion; clearTimeout(this.timer); window.removeEventListener('mirally-product-event', this.changed); super.disconnectedCallback(); }
  protected override updated(changed: PropertyValues) {
    if (changed.has('sessionKey') || changed.has('presented')) { ++this.epoch; ++this.permissionVersion; this.approvals=[]; this.error=''; this.sessionAutoApprove=null; this.defaultTrusted=false; this.permissionBusy=false; this.permissionError=''; void this.refresh(); }
  }
  private async refresh() {
    clearTimeout(this.timer);
    if (!this.isConnected || !this.presented || !this.conversationId) return;
    const epoch=++this.epoch, id=this.conversationId;
    try {
      const [approvals, permission]=await Promise.allSettled([
        rpcProduct<{approvals:Approval[]}>('action.approvals.list',{conversation_id:id}),
        rpcProduct<{session_auto_approve:boolean;default_trusted:boolean}>('action.permissions.get',{conversation_id:id}),
      ]);
      if (this.isConnected && epoch===this.epoch && id===this.conversationId) {
        if (approvals.status==='fulfilled') this.approvals=approvals.value.approvals;
        if (!this.permissionBusy && permission.status==='fulfilled' && typeof permission.value.session_auto_approve==='boolean') {
          this.sessionAutoApprove=permission.value.session_auto_approve;
          this.defaultTrusted=permission.value.default_trusted===true;
        }
      }
    } catch { /* Retry after a transient connection or initial host startup. */ }
    finally { if(this.isConnected && epoch===this.epoch && this.presented) this.timer=setTimeout(()=>void this.refresh(),3000); }
  }
  private async setPermission(enabled:boolean) {
    if (this.permissionBusy || this.sessionAutoApprove===null) return;
    const id=this.conversationId, version=++this.permissionVersion;
    ++this.epoch;
    clearTimeout(this.timer); this.permissionBusy=true; this.permissionError='';
    try {
      await rpcProduct('action.permissions.set',{conversation_id:id, enabled});
      if (this.isConnected && version===this.permissionVersion && id===this.conversationId) this.sessionAutoApprove=enabled;
    } catch(error) {
      if (this.isConnected && version===this.permissionVersion && id===this.conversationId) this.permissionError=errorMessage(error);
    } finally {
      if (this.isConnected && version===this.permissionVersion && id===this.conversationId) { this.permissionBusy=false; void this.refresh(); }
    }
  }
  private async decide(approval:Approval, allow:boolean) {
    if(this.busy) return;
    this.busy=true; this.error='';
    try { await rpcProduct('action.approvals.decide',{approval_id:approval.id, conversation_id:approval.conversation_id, allow}); await this.refresh(); }
    catch(error) { this.error=errorMessage(error); await this.refresh(); }
    finally { this.busy=false; }
  }
  override render() {
    if (!this.presented || !this.conversationId) return nothing;
    return html`<div class="mirally-session-permission">
      ${this.defaultTrusted?html`<span class="mirally-session-permission__trusted">全局持续信任已开启，电脑端动作自动批准。</span>`:nothing}
      <label><input type="checkbox" .checked=${this.sessionAutoApprove===true} ?disabled=${this.permissionBusy || this.sessionAutoApprove===null} @change=${(event:Event)=>this.setPermission((event.target as HTMLInputElement).checked)}> 本会话自动批准动作</label>
      <span>仅对当前会话有效，重启后失效；${this.defaultTrusted?'关闭本开关不会关闭全局信任；':''}微信仍受独立授权设置约束。</span>
      ${this.permissionError?html`<span role="alert">${this.permissionError}</span>`:nothing}
    </div>${this.approvals.map(approval=>html`<section class="mirally-action-approval" role="region" aria-label="等待确认的动作">
      <div class="mirally-action-approval__heading"><strong>${approval.title}</strong><span>需要确认</span></div>
      <pre tabindex="0" class="mirally-action-approval__detail">${approval.detail}</pre>
      ${this.error?html`<p role="alert">${this.error}</p>`:nothing}
      <div class="mirally-action-approval__actions"><button class="btn" type="button" ?disabled=${this.busy} @click=${()=>this.decide(approval,false)}>拒绝</button><button class="btn primary" type="button" ?disabled=${this.busy} @click=${()=>this.decide(approval,true)}>允许这次</button></div>
    </section>`)}`;
  }
}
