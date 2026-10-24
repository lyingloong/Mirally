import { html } from 'lit';
import { state } from 'lit/decorators.js';
import { renderSettingsPage, renderSettingsPageHeader, renderSettingsSection } from '../../components/settings-ui.ts';
import { renderSettingsWorkspace } from '../../components/settings-workspace.ts';
import { OpenClawLightDomElement } from '../../lit/openclaw-element.ts';
import { errorMessage, rpcProduct } from './api.ts';
import './product.css';
const draftKey='mirally.memory.draft.v1';
function readDraft(){try{const value=JSON.parse(sessionStorage.getItem(draftKey)||'{}');return value&&typeof value==='object'?value:{};}catch{return {};}}

/** Keep the existing Mirally memory browser and add explicit user corrections. */
class MirallyMemoryPage extends OpenClawLightDomElement {
  @state() private content=String(readDraft().content||'').slice(0,16000);
  @state() private memoryId=String(readDraft().memoryId||'').slice(0,512);
  @state() private busy=false;
  @state() private notice='';
  @state() private failed=false;
  private requestId=String(readDraft().requestId||'').slice(0,128);
  private requestContent=String(readDraft().requestContent||'').slice(0,16000);
  private saveDraft(){try{sessionStorage.setItem(draftKey,JSON.stringify({content:this.content,memoryId:this.memoryId,requestId:this.requestId,requestContent:this.requestContent}));}catch{}}
  private async save(event: SubmitEvent) {
    event.preventDefault();if(this.busy)return;
    this.busy=true;this.notice='';this.failed=false;
    const content=this.content.trim(), memoryId=this.memoryId.trim();
    if(this.requestContent!==content){this.requestContent=content;this.requestId=crypto.randomUUID();}
    this.saveDraft();
    try {await rpcProduct('memory.correct',{content,...(memoryId?{memory_id:memoryId}:{request_id:this.requestId})});
      if(!this.isConnected)return;this.content='';this.memoryId='';this.notice='已保存你明确提供的记忆。';
      this.requestId='';this.requestContent='';
      this.saveDraft();
      (window as any).__mirallyMemory?.refresh();
    }catch(error){if(this.isConnected){this.notice=errorMessage(error);this.failed=true;}}
    finally{if(this.isConnected)this.busy=false;}
  }
  override render(){return html`${renderSettingsPageHeader({title:'记忆',subtitle:'查看 Mirally 记住的内容，核对来源并随时纠正。'})}${renderSettingsWorkspace(renderSettingsPage(html`
    <section class="mirally-product-memory" data-mirally-memory-anchor .agentId=${'main'}><div class="memory-page__panel"></div></section>
    ${renderSettingsSection({title:'告诉 Mirally 该记住什么',description:'保存明确的事实或纠正已有记忆。保存会停止正在进行的回复，以免继续使用旧记忆。模型推测与用户确认分别标注；日常偏好可以在「设置 → 个性化」维护。'},html`
      <form class="mirally-product-form" @submit=${this.save}>
        <label class="mirally-product-field">记忆内容<textarea required maxlength="16000" data-testid="mirally-memory-content" .value=${this.content} @input=${(e:Event)=>{this.content=(e.target as HTMLTextAreaElement).value;this.saveDraft();}} ?disabled=${this.busy}></textarea></label>
        <details><summary>纠正已有记忆</summary><label class="mirally-product-field">记忆编号（可选）<input data-testid="mirally-memory-id" .value=${this.memoryId} @input=${(e:Event)=>{this.memoryId=(e.target as HTMLInputElement).value;this.saveDraft();}} ?disabled=${this.busy}></label></details>
        <button class="btn primary" data-testid="mirally-memory-save" ?disabled=${this.busy}>保存记忆</button>
        <p role=${this.failed?'alert':'status'} class="mirally-product-notice" ?data-error=${this.failed}>${this.notice}</p>
      </form>`)}
  `))}`;}
}
customElements.define('mirally-memory-page',MirallyMemoryPage);
