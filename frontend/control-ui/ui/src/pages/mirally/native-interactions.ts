import {html, nothing, type PropertyValues} from 'lit';
import {customElement, property, state} from 'lit/decorators.js';
import {OpenClawLightDomElement} from '../../lit/openclaw-element.ts';
import {rpcProduct, requestProduct, errorMessage} from './api.ts';
import './product.css';

type Question = {id:string; question:string; header?:string; detail?:string; multiSelect?:boolean;
  options?:{label:string; description?:string}[]; intent?:{kind:string; approve:string}};
type Request = {id:string; conversation_id:string; questions:Question[]; state:'open'|'continued'; can_reply:boolean; message?:string};
type Answer = {id:string; selected:string[]; custom?:string};
type Plan = {available:boolean; active:boolean; pending?:boolean};

/** Native DSH waits stay on their actual turn; the card only carries the human answer. */
@customElement('mirally-native-interactions')
export class MirallyNativeInteractions extends OpenClawLightDomElement {
  @property() sessionKey = '';
  @property({type:Boolean}) presented = true;
  @state() private requests: Request[] = [];
  @state() private drafts = new Map<string, Answer>();
  @state() private submitting = '';
  @state() private error = '';
  @state() private plan: Plan | null = null;
  @state() private planBusy = false;
  @state() private compactBusy = false;
  @state() private compactResult = '';
  private epoch = 0;
  private timer?: ReturnType<typeof setTimeout>;
  private get conversationId() { return this.sessionKey.replace(/^agent:main:/, ''); }
  private readonly changed = (event:Event) => {
    const detail = (event as CustomEvent).detail;
    if (detail?.type === 'interaction' && detail.conversation_id === this.conversationId) void this.refresh();
  };
  override connectedCallback() { super.connectedCallback(); window.addEventListener('mirally-product-event', this.changed); }
  override disconnectedCallback() { ++this.epoch; clearTimeout(this.timer); window.removeEventListener('mirally-product-event', this.changed); super.disconnectedCallback(); }
  protected override updated(changed:PropertyValues) {
    if (changed.has('sessionKey') || changed.has('presented')) {
      ++this.epoch; this.requests=[]; this.drafts=new Map(); this.error=''; this.submitting=''; this.plan=null; this.planBusy=false; this.compactBusy=false; this.compactResult='';
      void this.refresh();
    }
  }
  private async refresh() {
    clearTimeout(this.timer);
    if (!this.isConnected || !this.presented || !this.conversationId) return;
    const epoch=this.epoch, id=this.conversationId;
    try {
      const [questions,plan]=await Promise.allSettled([
        rpcProduct<{questions:Request[]}>('interaction.questions.list',{conversation_id:id}),
        rpcProduct<Plan>('interaction.plan.get',{conversation_id:id}),
      ]);
      if (!this.isConnected || epoch!==this.epoch || id!==this.conversationId) return;
      if (questions.status==='fulfilled') {
        this.requests=questions.value.questions;
        const live=new Set(this.requests.flatMap(request=>request.questions.map(question=>this.key(request,question))));
        this.drafts=new Map([...this.drafts].filter(([key])=>live.has(key)));
      }
      if (plan.status==='fulfilled' && !this.planBusy) this.plan=plan.value;
    } finally {
      if (this.isConnected && epoch===this.epoch && this.presented) this.timer=setTimeout(()=>void this.refresh(),3000);
    }
  }
  private key(request:Request, question:Question) { return JSON.stringify([request.id,question.id]); }
  private draft(request:Request, question:Question):Answer { return this.drafts.get(this.key(request,question)) ?? {id:question.id,selected:[]}; }
  private select(request:Request, question:Question, label:string, checked:boolean) {
    const answer=this.draft(request,question);
    const selected=question.multiSelect ? [...new Set([...answer.selected.filter(item=>item!==label),...(checked?[label]:[])])] : checked?[label]:[];
    this.drafts=new Map(this.drafts).set(this.key(request,question),{...answer,selected});
  }
  private write(request:Request, question:Question, custom:string) {
    this.drafts=new Map(this.drafts).set(this.key(request,question),{...this.draft(request,question),custom});
  }
  private complete(request:Request) { return request.questions.every(question=>{const answer=this.draft(request,question); return answer.selected.length>0 || Boolean(answer.custom?.trim());}); }
  private async answer(request:Request) {
    if (this.submitting || !request.can_reply || !this.complete(request)) return;
    const epoch=this.epoch, id=this.conversationId;
    this.submitting=request.id; this.error='';
    try {
      await rpcProduct('interaction.questions.answer',{conversation_id:id,question_id:request.id,
        answers:request.questions.map(question=>this.draft(request,question))});
      if (this.isConnected && epoch===this.epoch && id===this.conversationId) await this.refresh();
    } catch(error) {
      if (this.isConnected && epoch===this.epoch && id===this.conversationId) {this.error=errorMessage(error);await this.refresh();}
    } finally { if (this.isConnected && epoch===this.epoch && id===this.conversationId) this.submitting=''; }
  }
  private async setPlan(active:boolean) {
    if (this.planBusy) return;
    const epoch=this.epoch, id=this.conversationId; this.planBusy=true; this.error='';
    try {
      const result=await rpcProduct<Plan>('interaction.plan.set',{conversation_id:id,active});
      if (this.isConnected && epoch===this.epoch && id===this.conversationId) this.plan=result;
    } catch(error) {if (this.isConnected && epoch===this.epoch && id===this.conversationId) this.error=errorMessage(error);}
    finally {if (this.isConnected && epoch===this.epoch && id===this.conversationId) this.planBusy=false;}
  }
  private async compact() {
    if (this.compactBusy) return;
    const epoch=this.epoch, id=this.conversationId; this.compactBusy=true; this.compactResult=''; this.error='';
    try {
      const outcome=await requestProduct<{result:{kind:string; text?:string}}>(`/api/sessions/${encodeURIComponent(id)}/native/commands`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({line:'/compact'})});
      if (this.isConnected && epoch===this.epoch && id===this.conversationId) {
        if (outcome.result.kind==='error') this.error=outcome.result.text??'压缩未完成。';
        else this.compactResult=outcome.result.text??'已完成上下文压缩。';
      }
    } catch(error) {if (this.isConnected && epoch===this.epoch && id===this.conversationId) this.error=errorMessage(error);}
    finally {if (this.isConnected && epoch===this.epoch && id===this.conversationId) this.compactBusy=false;}
  }
  override render() {
    if (!this.presented || !this.conversationId) return nothing;
    return html`${this.plan?.available?html`<div class="mirally-session-permission"><label><input type="checkbox" .checked=${this.plan.pending ?? this.plan.active} ?disabled=${this.planBusy} @change=${(event:Event)=>this.setPlan((event.target as HTMLInputElement).checked)}> 计划模式</label><span>${this.plan.pending!==undefined?'将在下一步应用；':''}先调研与设计，审核计划后再实施。</span><button type="button" class="btn" ?disabled=${this.compactBusy} @click=${()=>this.compact()}>${this.compactBusy?'正在压缩…':'压缩上下文'}</button></div>`:nothing}
      ${this.compactResult?html`<p role="status">${this.compactResult}</p>`:nothing}
      ${this.error?html`<p class="mirally-native-question__error" role="alert">${this.error}</p>`:nothing}
      ${this.requests.map(request=>html`<section class="mirally-native-question" role="region" aria-label="Mirally 等待你的回答">
        <div class="mirally-action-approval__heading"><strong>${request.questions.some(question=>question.intent?.kind==='plan-review')?'审核计划':'需要你的回答'}</strong><span>${request.state==='open'?'正在等待':'原回合已继续'}</span></div>
        ${request.questions.map(question=>{const answer=this.draft(request,question);return html`<fieldset ?disabled=${!request.can_reply || Boolean(this.submitting)}><legend>${question.header?`${question.header} · `:''}${question.question}</legend>
          ${question.detail?html`<pre class="mirally-native-question__detail" tabindex="0">${question.detail}</pre>`:nothing}
          <div class="mirally-native-question__options">${(question.options??[]).map(option=>html`<label><input type=${question.multiSelect?'checkbox':'radio'} name=${this.key(request,question)} .checked=${answer.selected.includes(option.label)} @change=${(event:Event)=>this.select(request,question,option.label,(event.target as HTMLInputElement).checked)}><span><strong>${option.label}</strong>${option.description?html`<small>${option.description}</small>`:nothing}</span></label>`)}</div>
          <label class="mirally-native-question__custom">${question.options?.length?'补充说明或其他回答':'你的回答'}<textarea rows="2" maxlength="24000" .value=${answer.custom??''} @input=${(event:Event)=>this.write(request,question,(event.target as HTMLTextAreaElement).value)}></textarea></label>
        </fieldset>`;})}
        ${request.message?html`<p>${request.message}</p>`:nothing}
        ${request.can_reply?html`<div class="mirally-action-approval__actions"><button type="button" class="btn primary" ?disabled=${Boolean(this.submitting)||!this.complete(request)} @click=${()=>this.answer(request)}>提交回答</button></div>`:nothing}
      </section>`)}`;
  }
}
