import {consume} from '@lit/context';
import {html, nothing} from 'lit';
import {state} from 'lit/decorators.js';
import {applicationContext, type ApplicationContext} from '../../app/context.ts';
import {renderSettingsSection} from '../../components/settings-ui.ts';
import {MirallyLightDomElement} from '../../lit/mirally-element.ts';
import {sessionNavigationTarget} from '../../lib/sessions/route-navigation.ts';
import {errorMessage, rpcProduct} from './api.ts';
import './product.css';

type CalendarEvent = {event_id: string; title: string; start_at_utc_ms: number; end_at_utc_ms?: number | null;
  time_zone: string; version: number; status: string; notes?:string; all_day?:boolean; conversation_id?: string | null; reminder_offsets_minutes?: number[]};
type CalendarSettings = {time_zone: string | null; confirmed: boolean; detected_time_zone: string | null};
const zoneChangedNotice = '时区已改变，请重新打开编辑确认时间。';

class MirallyCalendarPanel extends MirallyLightDomElement {
  @consume({context:applicationContext, subscribe:true}) private context!: ApplicationContext;
  @state() private events: CalendarEvent[] = [];
  @state() private settings: CalendarSettings | null = null;
  @state() private loading = true;
  @state() private hasLoaded = false;
  @state() private busy = false;
  @state() private failure = '';
  @state() private failureDetail = '';
  @state() private editing: CalendarEvent | null = null;
  @state() private editingZone = '';
  @state() private title = '';
  @state() private start = '';
  @state() private end = '';
  @state() private notes = '';
  @state() private reminder = '';
  private epoch = 0;
  private readonly changed = (event:Event) => {
    if (['calendar','inbox'].includes((event as CustomEvent).detail?.type) && !this.busy) void this.refresh();
  };
  override connectedCallback() { super.connectedCallback(); window.addEventListener('mirally-product-event',this.changed); void this.refresh(); }
  override disconnectedCallback() { ++this.epoch; window.removeEventListener('mirally-product-event',this.changed); super.disconnectedCallback(); }
  private async refresh() {
    const epoch=++this.epoch; this.loading=true; this.failure=''; this.failureDetail='';
    try {
      const [settings, result] = await Promise.all([rpcProduct<CalendarSettings>('calendar.settings.get'),
        rpcProduct<{events:CalendarEvent[]}>('calendar.event.list',{start_at:new Date().toISOString(),limit:100})]);
      if (!this.isConnected || epoch!==this.epoch) return;
      if (!settings || typeof settings.confirmed!=='boolean' || (settings.time_zone!==null && typeof settings.time_zone!=='string')) throw new Error('服务返回的日历状态不完整，请刷新重试。');
      if (!result || !Array.isArray(result.events)) throw new Error('服务没有返回有效日程，请刷新重试。');
      this.settings=settings; this.events=result.events; this.hasLoaded=true; this.failure=''; this.failureDetail='';
      if (this.editing && this.editingZone && settings.time_zone && settings.time_zone!==this.editingZone) { this.editing=null; this.failure=zoneChangedNotice; }
    } catch(error) { if (this.isConnected && epoch===this.epoch) {
      this.failure=this.hasLoaded?'刷新失败，保留上次日程，请刷新重试。':'日程读取失败，请刷新重试。';
      this.failureDetail=errorMessage(error);
    } }
    finally { if(this.isConnected && epoch===this.epoch) this.loading=false; }
  }
  private async cancel(event: CalendarEvent) {
    if(this.busy)return; this.busy=true; this.failure=''; this.failureDetail='';
    try { await rpcProduct('calendar.event.cancel',{event_id:event.event_id,expected_version:event.version}); await this.refresh(); }
    catch(error) { this.failure=errorMessage(error); this.failureDetail=''; } finally {this.busy=false;}
  }
  private localValue(value:number,zone:string) {
    const fields=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value)).map(part=>[part.type,part.value]));
    return `${fields.year}-${fields.month}-${fields.day}T${fields.hour}:${fields.minute}`;
  }
  private edit(event:CalendarEvent) {
    const zone=this.settings?.time_zone || event.time_zone;
    this.editing=event;this.editingZone=zone;this.title=event.title;this.notes=event.notes || '';
    this.start=this.localValue(event.start_at_utc_ms,zone);
    this.end=event.end_at_utc_ms ? this.localValue(event.end_at_utc_ms,zone) : '';
    this.reminder=(event.reminder_offsets_minutes || []).join(', ');this.failure='';this.failureDetail='';
  }
  private async save(event:SubmitEvent) {
    event.preventDefault();const selected=this.editing;if(!selected||this.busy)return;
    const zone=this.editingZone||this.settings?.time_zone||selected.time_zone;
    const offsets=this.reminder.trim() ? this.reminder.split(/[,，\s]+/).map(Number) : [];
    if(offsets.some(value=>!Number.isSafeInteger(value)||value<0)) {this.failure='提醒时间请输入提前的分钟数。';return;}
    this.busy=true;this.failure='';this.failureDetail='';
    let staleDraft=false;
    try {
      const start=await rpcProduct<{iso:string;time_zone:string}>('calendar.time.resolve',{local_time:this.start});
      const end=this.end?await rpcProduct<{iso:string;time_zone:string}>('calendar.time.resolve',{local_time:this.end}):null;
      if(start.time_zone!==zone||(end&&end.time_zone!==zone)) { staleDraft=true; throw new Error(zoneChangedNotice); }
      await rpcProduct('calendar.event.update',{event_id:selected.event_id,expected_version:selected.version,
        expected_time_zone:zone,patch:{title:this.title.trim(),notes:this.notes,start_at:start.iso,end_at:end?.iso??null,reminder_offsets_minutes:offsets}});
      this.editing=null;await this.refresh();
    } catch(error) {if(staleDraft)this.editing=null;
      this.failure=errorMessage(error);this.failureDetail='';} finally {this.busy=false;}
  }
  private openConversation(event:CalendarEvent) {
    if (!event.conversation_id) return;
    const target=sessionNavigationTarget({context:this.context,face:'chat',sessionKey:'agent:main:'+event.conversation_id});
    this.context.navigate('chat',target.options);
  }
  private format(event:CalendarEvent) {
    try { return new Intl.DateTimeFormat('zh-CN',{timeZone:this.settings?.time_zone || event.time_zone,year:'numeric',month:'long',day:'numeric',weekday:'short',hour:'2-digit',minute:'2-digit'}).format(new Date(event.start_at_utc_ms)); }
    catch { return '时间待确认'; }
  }
  override render() {
    const events=this.events.filter(event=>event.status!=='cancelled').sort((a,b)=>a.start_at_utc_ms-b.start_at_utc_ms);
    return html`${this.failure?html`<p role="alert" class="mirally-product-notice" data-error>${this.failure}</p>`:nothing}
      ${this.failure&&this.failureDetail?html`<details class="mirally-product-notice"><summary>错误详情</summary><pre class="mirally-product-body">${this.failureDetail}</pre></details>`:nothing}
      ${this.settings&&!this.settings.confirmed ? html`<p class="mirally-product-notice">请先在通用设置中确认时区。</p>`:nothing}
      ${renderSettingsSection({title:'日程',actions:html`<button class="btn mirally-product-refresh" ?disabled=${this.loading||this.busy} @click=${()=>this.refresh()}>${this.loading&&this.hasLoaded?'刷新中…':'刷新'}</button>`},
        html`<div aria-busy=${String(this.loading)}>
        ${this.loading&&!this.hasLoaded ? html`<p role="status" class="mirally-product-empty">正在读取…</p>`
        : this.failure&&!this.hasLoaded ? nothing
        : !events.length
        ? html`<p class="mirally-product-empty">还没有日程。可以在对话中告诉 Mirally。</p>`
        : events.map(event=>html`<article class="mirally-product-item" data-testid="mirally-calendar-event">
          ${this.editing?.event_id===event.event_id?html`<form class="mirally-product-form" @submit=${this.save}>
            <label class="mirally-product-field">名称<input required maxlength="1024" .value=${this.title} @input=${(e:Event)=>this.title=(e.target as HTMLInputElement).value}></label>
            <label class="mirally-product-field">开始${this.editingZone?`（${this.editingZone}）`:''}<input type="datetime-local" required .value=${this.start} @input=${(e:Event)=>this.start=(e.target as HTMLInputElement).value}></label>
            <label class="mirally-product-field">结束<input type="datetime-local" .value=${this.end} @input=${(e:Event)=>this.end=(e.target as HTMLInputElement).value}></label>
            <label class="mirally-product-field">备注<textarea rows="2" maxlength="16000" .value=${this.notes} @input=${(e:Event)=>this.notes=(e.target as HTMLTextAreaElement).value}></textarea></label>
            <label class="mirally-product-field">提前提醒（分钟，可用逗号分隔）<input inputmode="numeric" .value=${this.reminder} @input=${(e:Event)=>this.reminder=(e.target as HTMLInputElement).value}></label>
            <div class="mirally-product-actions"><button type="submit" class="btn primary" ?disabled=${this.busy}>保存</button><button type="button" class="btn" ?disabled=${this.busy} @click=${()=>this.editing=null}>取消</button></div>
          </form>`:html`<h3>${event.title}</h3><p class="mirally-product-meta">${this.format(event)}${event.reminder_offsets_minutes?.length ? ` · ${event.reminder_offsets_minutes.map(value=>value===0?'开始时提醒':`提前 ${value} 分钟提醒`).join('、')}` : ''}</p>
          ${event.notes?html`<p class="mirally-product-body">${event.notes}</p>`:nothing}
          <div class="mirally-product-actions">${event.conversation_id?html`<button class="btn" @click=${()=>this.openConversation(event)}>接着聊</button>`:nothing}
          <button class="btn" ?disabled=${this.busy} @click=${()=>this.edit(event)}>编辑</button>
          <button class="btn" ?disabled=${this.busy} @click=${()=>this.cancel(event)}>取消日程</button></div>`}
        </article>`)}</div>`)}`;
  }
}
customElements.define('mirally-calendar-panel',MirallyCalendarPanel);
