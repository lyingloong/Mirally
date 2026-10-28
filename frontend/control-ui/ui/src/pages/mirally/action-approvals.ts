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
  private epoch = 0;
  private readVersion = 0;
  private timer?: ReturnType<typeof setTimeout>;
  private get conversationId() { return this.sessionKey.replace(/^agent:main:/, ''); }
  private readonly changed = (event: Event) => {
    const detail = (event as CustomEvent).detail;
    if (detail?.action === 'approval-changed' && detail.conversation_id === this.conversationId) void this.refresh();
  };
  override connectedCallback() { super.connectedCallback(); window.addEventListener('mirally-product-event', this.changed); }
  override disconnectedCallback() { ++this.epoch; clearTimeout(this.timer); window.removeEventListener('mirally-product-event', this.changed); super.disconnectedCallback(); }
  protected override updated(changed: PropertyValues) {
    if (changed.has('sessionKey') || changed.has('presented')) { ++this.epoch; this.approvals=[]; this.error=''; this.busy=false; void this.refresh(); }
  }
  private async refresh() {
    clearTimeout(this.timer);
    if (!this.isConnected || !this.presented || !this.conversationId) return;
    const epoch=this.epoch, version=++this.readVersion, id=this.conversationId;
    try {
      const result=await rpcProduct<{approvals:Approval[]}>('action.approvals.list',{conversation_id:id});
      if (this.isConnected && epoch===this.epoch && version===this.readVersion && id===this.conversationId) {
        this.approvals=result.approvals;
      }
    } catch { /* Retry after a transient connection or initial host startup. */ }
    finally { if(this.isConnected && epoch===this.epoch && version===this.readVersion && this.presented) this.timer=setTimeout(()=>void this.refresh(),3000); }
  }
  private async decide(approval:Approval, allow:boolean) {
    if(this.busy) return;
    const epoch=this.epoch, id=this.conversationId;
    this.busy=true; this.error='';
    try { await rpcProduct('action.approvals.decide',{approval_id:approval.id, conversation_id:approval.conversation_id, allow}); }
    catch(error) { if (this.isConnected && epoch===this.epoch && id===this.conversationId) this.error=errorMessage(error); }
    finally { if (this.isConnected && epoch===this.epoch && id===this.conversationId) { this.busy=false; void this.refresh(); } }
  }
  override render() {
    if (!this.presented || !this.conversationId) return nothing;
    return html`${this.approvals.map(approval=>html`<section class="mirally-action-approval" role="region" aria-label="等待确认的动作">
      <div class="mirally-action-approval__heading"><strong>${approval.title}</strong><span>需要确认</span></div>
      <pre tabindex="0" class="mirally-action-approval__detail">${approval.detail}</pre>
      ${this.error?html`<p role="alert">${this.error}</p>`:nothing}
      <div class="mirally-action-approval__actions"><button class="btn" type="button" ?disabled=${this.busy} @click=${()=>this.decide(approval,false)}>拒绝</button><button class="btn primary" type="button" ?disabled=${this.busy} @click=${()=>this.decide(approval,true)}>允许这次</button></div>
    </section>`)}`;
  }
}
