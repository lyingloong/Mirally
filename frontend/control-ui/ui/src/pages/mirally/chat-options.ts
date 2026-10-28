import {html, nothing, type PropertyValues} from 'lit';
import {ref} from 'lit/directives/ref.js';
import {customElement, property, state} from 'lit/decorators.js';
import {icons} from '../../components/icons.ts';
import {syncDropdownItemRadio} from '../../components/web-awesome.ts';
import {OpenClawLightDomElement} from '../../lit/openclaw-element.ts';
import {rpcProduct, openProductPage, errorMessage} from './api.ts';
import './product.css';

type Permission = {session_auto_approve:boolean; default_trusted:boolean};
type Plan = {available:boolean; active:boolean; pending?:boolean};

/** Composer choices show effective product policy, never an ineffective off switch. */
@customElement('mirally-chat-options')
export class MirallyChatOptions extends OpenClawLightDomElement {
  @property() sessionKey = '';
  @property({type:Boolean}) presented = true;
  @state() private permission: Permission | null = null;
  @state() private plan: Plan | null = null;
  @state() private busy = false;
  @state() private loading = true;
  @state() private error = '';
  private epoch = 0;
  private readVersion = 0;
  private timer?: ReturnType<typeof setTimeout>;
  private get conversationId() { return this.sessionKey.replace(/^agent:main:/, ''); }
  private get automatic() { return Boolean(this.permission?.default_trusted || this.permission?.session_auto_approve); }
  private readonly changed = (event:Event) => {
    const detail = (event as CustomEvent).detail;
    if (detail?.conversation_id === this.conversationId &&
        (detail.type === 'interaction' || detail.action === 'permission-changed')) void this.refresh();
  };
  override connectedCallback() { super.connectedCallback(); window.addEventListener('mirally-product-event', this.changed); }
  override disconnectedCallback() { ++this.epoch; ++this.readVersion; clearTimeout(this.timer); window.removeEventListener('mirally-product-event', this.changed); super.disconnectedCallback(); }
  protected override updated(changed:PropertyValues) {
    if (changed.has('sessionKey') || changed.has('presented')) {
      ++this.epoch; ++this.readVersion; this.permission=null; this.plan=null; this.busy=false; this.loading=true; this.error=''; void this.refresh();
    }
  }
  private async refresh() {
    clearTimeout(this.timer);
    if (!this.isConnected || !this.presented || !this.conversationId || this.busy) return;
    const epoch=this.epoch, version=++this.readVersion, id=this.conversationId;
    const [permission,plan]=await Promise.allSettled([
      Promise.resolve().then(()=>rpcProduct<Permission>('action.permissions.get',{conversation_id:id})),
      Promise.resolve().then(()=>rpcProduct<Plan>('interaction.plan.get',{conversation_id:id})),
    ]);
    if (!this.isConnected || epoch!==this.epoch || version!==this.readVersion || id!==this.conversationId) return;
    // A failed read is unknown, rather than the last policy presented as current.
    this.permission=permission.status==='fulfilled'?permission.value:null;
    this.plan=plan.status==='fulfilled'?plan.value:null;
    this.loading=false;
    this.timer=setTimeout(()=>void this.refresh(),3000);
  }
  private async change(kind:'permission'|'plan', enabled:boolean) {
    if (this.busy || (kind==='permission' && (!this.permission || this.permission.default_trusted)) ||
        (kind==='plan' && !this.plan?.available)) return;
    const epoch=this.epoch, id=this.conversationId;
    ++this.readVersion; clearTimeout(this.timer); this.busy=true; this.error='';
    try {
      if (kind==='permission') {
        const result=await rpcProduct<Permission>('action.permissions.set',{conversation_id:id,enabled});
        if (this.isConnected && epoch===this.epoch && id===this.conversationId) this.permission=result;
      } else {
        const result=await rpcProduct<Plan>('interaction.plan.set',{conversation_id:id,active:enabled});
        if (this.isConnected && epoch===this.epoch && id===this.conversationId) this.plan=result;
      }
    } catch(error) {
      if (this.isConnected && epoch===this.epoch && id===this.conversationId) this.error=errorMessage(error);
    } finally {
      if (this.isConnected && epoch===this.epoch && id===this.conversationId) { this.busy=false; void this.refresh(); }
    }
  }
  private async select(value:string) {
    if (value==='ask' || value==='session') await this.change('permission',value==='session');
    else if (value==='execute' || value==='plan') await this.change('plan',value==='plan');
    else if (value==='settings') {
      try { await openProductPage('settings'); } catch(error) { this.error=errorMessage(error); }
    } else if (value==='retry') { this.error=''; await this.refresh(); }
  }
  private choice(value:string, label:string, checked:boolean, detail='') {
    return html`<wa-dropdown-item value=${value} ?disabled=${this.busy} role="menuitemradio" aria-checked=${String(checked)}
      ${ref(element=>syncDropdownItemRadio(element,checked))}>${label}
      ${detail?html`<span slot="details">${detail}</span>`:nothing}
      <span slot="icon" aria-hidden="true">${checked?icons.check:nothing}</span>
    </wa-dropdown-item>`;
  }
  override render() {
    if (!this.presented || !this.conversationId) return nothing;
    const chosenPlan=this.plan?.pending ?? this.plan?.active;
    const mode=this.plan?.available ? `${chosenPlan?'计划':'执行'}${this.plan.pending!==undefined?'（下轮）':''}` : '';
    const permission=this.permission ? (this.automatic?'自动批准':'逐次确认') : this.loading?'读取中…':'权限不可用';
    return html`<wa-dropdown class="mirally-chat-options" placement="top-start" aria-label="会话模式与权限"
      @wa-select=${(event:CustomEvent<{item:{value:string}}>)=>void this.select(event.detail.item.value)}>
      <button slot="trigger" type="button" class="mirally-chat-options__trigger" aria-label=${`会话模式与权限：${[mode,permission].filter(Boolean).join('，')}`} aria-busy=${String(this.busy)}>
        <span>${[mode,permission].filter(Boolean).join(' · ')}</span>${icons.chevronDown}
      </button>
      ${this.plan?.available?html`<div class="mirally-chat-options__heading">运行模式${this.plan.pending!==undefined?` · 当前${this.plan.active?'计划':'执行'}`:''}</div>
        ${this.choice('execute','执行',!chosenPlan)}${this.choice('plan','计划',Boolean(chosenPlan))}<wa-divider></wa-divider>`:nothing}
      <div class="mirally-chat-options__heading">电脑端动作</div>
      ${this.permission ? this.permission.default_trusted
        ? html`<div class="mirally-chat-options__source">${icons.check}<span>自动批准 · 全局设置</span></div>`
        : html`${this.choice('ask','逐次确认',!this.automatic)}${this.choice('session','自动批准本会话',this.automatic,'重启后恢复')}`
        : this.loading?html`<div class="mirally-chat-options__heading" role="status">正在读取权限…</div>`:html`<wa-dropdown-item value="retry">重新读取权限</wa-dropdown-item>`}
      <wa-dropdown-item value="settings">权限设置…</wa-dropdown-item>
    </wa-dropdown>${this.error?html`<span class="mirally-chat-options__error" role="alert">${this.error}</span>`:nothing}`;
  }
}
