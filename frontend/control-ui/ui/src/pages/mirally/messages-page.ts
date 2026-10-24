import { consume } from '@lit/context';
import { html, nothing, type PropertyValues } from 'lit';
import { property, state } from 'lit/decorators.js';
import { applicationContext, type ApplicationContext } from '../../app/context.ts';
import { renderSettingsPage, renderSettingsPageHeader, renderSettingsSection } from '../../components/settings-ui.ts';
import { renderSettingsWorkspace } from '../../components/settings-workspace.ts';
import { OpenClawLightDomElement } from '../../lit/openclaw-element.ts';
import { sessionNavigationTarget } from '../../lib/sessions/route-navigation.ts';
import { errorMessage, rpcProduct } from './api.ts';
import './product.css';

type Item = Record<string, any>;
const statuses: Record<string, string> = {pending:'待提醒', fulfilled:'已送达', cancelled:'已取消', expired:'已过期', unread:'未读', read:'已读', dismissed:'已忽略', replied:'已回复'};
const date = (value: number) => Number.isFinite(value) ? new Date(value).toLocaleString() : '时间未记录';
const draftKey='mirally.reminder.draft.v1';
function readDraft() { try {const value=JSON.parse(sessionStorage.getItem(draftKey)||'{}');return value&&typeof value==='object'?value:{};}catch{return {};} }

/** Messages and reminders are projections of the product owner, never chat copies. */
class MirallyMessagesPage extends OpenClawLightDomElement {
  @consume({context: applicationContext, subscribe: true}) private context!: ApplicationContext;
  @property() page: 'inbox'|'reminders' = 'inbox';
  @state() private items: Item[] = [];
  @state() private loading = true;
  @state() private busy = false;
  @state() private notice = '';
  @state() private failure = '';
  @state() private reminderTitle = String(readDraft().title||'').slice(0,512);
  @state() private time = String(readDraft().time||'').slice(0,32);
  @state() private unreadOnly = false;
  private epoch = 0;
  private view = 0;
  private requestId = String(readDraft().requestId||'').slice(0,128);
  private requestContent = String(readDraft().requestContent||'').slice(0,2048);
  private saveDraft() {try{sessionStorage.setItem(draftKey,JSON.stringify({title:this.reminderTitle,time:this.time,requestId:this.requestId,requestContent:this.requestContent}));}catch{} }
  private readonly onProductEvent = (event: Event) => {
    if ((event as CustomEvent).detail?.type === 'inbox' && this.page === 'inbox' && !this.busy) void this.refresh();
  };
  override connectedCallback() { super.connectedCallback(); window.addEventListener('mirally-product-event', this.onProductEvent); }
  override disconnectedCallback() { ++this.epoch;++this.view; window.removeEventListener('mirally-product-event', this.onProductEvent); super.disconnectedCallback(); }
  protected override updated(changed: PropertyValues) { if (changed.has('page')) { ++this.view;this.busy=false;this.items=[]; this.failure=''; this.notice=''; void this.refresh(); } }
  private async refresh() {
    const epoch=++this.epoch, page=this.page; this.loading=true;
    try { const result=await rpcProduct<Item>(page==='inbox'?'inbox.list':'commitment.list');
      if (!this.isConnected || epoch!==this.epoch) return;
      this.items=page==='inbox'?result.items:result.commitments; this.failure='';
      if (!Array.isArray(this.items)) throw new Error('服务没有返回有效列表，请刷新重试。');
    } catch(error) { if (this.isConnected && epoch===this.epoch) { this.failure=errorMessage(error); this.items=[]; } }
    finally { if (this.isConnected && epoch===this.epoch) this.loading=false; }
  }
  private async mutate(action:()=>Promise<unknown>, success: string) {
    if(this.busy)return;this.busy=true;this.failure='';this.notice='';
    const view=this.view;
    try { await action(); if(this.isConnected&&this.view===view) {this.notice=success;await this.refresh();} }
    catch(error){if(this.isConnected&&this.view===view)this.failure=errorMessage(error);}
    finally{if(this.isConnected&&this.view===view)this.busy=false;}
  }
  private async create(event: SubmitEvent) {
    event.preventDefault(); const title=this.reminderTitle.trim(),due=new Date(this.time).getTime();
    const content=JSON.stringify({title,due});
    if (!title || !Number.isFinite(due) || due<=Date.now()&&!(this.requestId&&content===this.requestContent)) { this.failure='填写提醒内容和未来的本地时间。'; return; }
    if(content!==this.requestContent){this.requestContent=content;this.requestId=crypto.randomUUID();}
    this.saveDraft();const view=this.view;
    await this.mutate(async()=>{
      await rpcProduct('commitment.create',{title,body:title,due_at_utc_ms:due,channel:'inbox',request_id:this.requestId});
      if(this.view===view){this.reminderTitle='';this.time='';this.requestContent='';this.requestId='';this.saveDraft();}
    },'提醒已建立，到时会送到 Mirally 消息页。');
  }
  private reminderForm() { return renderSettingsSection({title:'添加提醒',description:'按本地时间设置。Mirally 运行期间送到消息页；退出或设备关机时无法实时投递，重新启动后检查仍有效的提醒。'},html`
    <form class="mirally-product-form" @submit=${this.create}>
      <label class="mirally-product-field">提醒什么<input data-testid="mirally-reminder-title" required maxlength="512" .value=${this.reminderTitle} @input=${(e:Event)=>{this.reminderTitle=(e.target as HTMLInputElement).value;this.saveDraft();}} ?disabled=${this.busy}></label>
      <label class="mirally-product-field">何时提醒（本地时间）<input data-testid="mirally-reminder-time" type="datetime-local" required .value=${this.time} @input=${(e:Event)=>{this.time=(e.target as HTMLInputElement).value;this.saveDraft();}} ?disabled=${this.busy}></label>
      <div class="mirally-product-actions"><button class="btn primary" data-testid="mirally-reminder-create" type="submit" ?disabled=${this.busy||this.loading}>建立提醒</button></div>
    </form>`); }
  private openConversation(item: Item) {
    if(!item.conversation_id || item.withdrawn)return;
    const target=sessionNavigationTarget({context:this.context,face:'chat',sessionKey:'agent:main:'+item.conversation_id});
    this.context.navigate('chat',target.options);
  }
  override render() {
    const reminders=this.page==='reminders';
    const list=this.items.filter(item=>reminders||!this.unreadOnly||item.state==='unread');
    const notice=html`${this.failure?html`<p role="alert" class="mirally-product-notice" data-error>${this.failure}</p>`:nothing}${this.notice?html`<p role="status" class="mirally-product-notice">${this.notice}</p>`:nothing}`;
    return html`${renderSettingsPageHeader({title:reminders?'提醒':'消息',subtitle:reminders?'记下希望 Mirally 在何时提醒你的事。':'来自 Mirally 的提醒与主动联系。'})}${renderSettingsWorkspace(renderSettingsPage(html`
      ${notice}${reminders?this.reminderForm():nothing}
      ${renderSettingsSection({title:reminders?'我的提醒':'Mirally 发来的消息',description:reminders?'提醒独立于一般主动联系开关。':'到期提醒与获准的主动联系会保存在这里。打开页面不会自动标记已读。',actions:html`<button class="btn" @click=${()=>this.refresh()} ?disabled=${this.loading||this.busy}>刷新</button>`},html`
        ${!reminders?html`<div class="mirally-product-item"><label><input type="checkbox" data-testid="mirally-inbox-unread" .checked=${this.unreadOnly} @change=${(e:Event)=>this.unreadOnly=(e.target as HTMLInputElement).checked}>只看未读</label></div>`:nothing}
        ${this.loading?html`<p role="status" class="mirally-product-empty">正在读取…</p>`:!list.length?html`<p class="mirally-product-empty">${this.failure?'读取失败，刷新后重试。':reminders?'还没有提醒。':'这里暂时没有消息。'}</p>`:list.map(item=>html`
          <article class="mirally-product-item" data-testid=${reminders?'mirally-reminder-item':'mirally-inbox-item'}>
            <h3>${item.title}</h3>${item.body&&item.body!==item.title?html`<p class="mirally-product-body">${item.body}</p>`:nothing}
            <p class="mirally-product-meta">${statuses[reminders?item.status:item.state]||'状态待核对'} · ${date(reminders?item.due_at:item.updated_at)}</p>
            <div class="mirally-product-actions">
              ${reminders&&item.status==='pending'?html`<button class="btn" ?disabled=${this.busy} @click=${()=>this.mutate(()=>rpcProduct('commitment.cancel',{commitment_id:item.id}),'提醒已取消。')}>取消提醒</button>`:nothing}
              ${!reminders&&item.state==='unread'?html`<button class="btn" data-testid="mirally-inbox-read" ?disabled=${this.busy} @click=${()=>this.mutate(()=>rpcProduct('inbox.ack',{delivery_id:item.delivery_id,state:'read'}),'已标记为已读。')}>标记已读</button>`:nothing}
              ${!reminders&&item.conversation_id&&!item.withdrawn?html`<button class="btn" @click=${()=>this.openConversation(item)}>接着聊</button>`:nothing}
            </div>
          </article>`)}
      `)}
    `))}`;
  }
}
customElements.define('mirally-messages-page',MirallyMessagesPage);
