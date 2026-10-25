import {html, nothing} from 'lit';
import {customElement, state} from 'lit/decorators.js';
import {OpenClawLightDomElement} from '../../lit/openclaw-element.ts';
import {renderSettingsSection, renderSettingsRow, renderSettingsEmpty} from '../../components/settings-ui.ts';
import {rpcProduct, errorMessage} from './api.ts';

type Entry={id:string;session_id:string;bytes:number;archived:boolean};
type Page={messages:{role:string;text:string;timestamp?:unknown;truncated?:boolean}[];next_cursor:number|null};

@customElement('mirally-legacy-history')
export class MirallyLegacyHistory extends OpenClawLightDomElement {
  @state() private entries:Entry[]=[];
  @state() private selected='';
  @state() private result:Page|null=null;
  @state() private error='';
  @state() private busy=false;
  @state() private loaded=false;
  private epoch=0;
  override connectedCallback(){super.connectedCallback();void this.reload();}
  override disconnectedCallback(){++this.epoch;super.disconnectedCallback();}
  private async reload(){
    const epoch=++this.epoch;this.busy=true;this.error='';
    try{const value=await rpcProduct<{sessions:Entry[]}>('legacy.history');if(!this.isConnected||epoch!==this.epoch)return;this.entries=value.sessions;this.loaded=true;}
    catch(error){if(epoch===this.epoch)this.error=errorMessage(error);}
    finally{if(epoch===this.epoch)this.busy=false;}
  }
  private async read(id:string,cursor=0){
    const epoch=++this.epoch;this.busy=true;this.error='';
    if(cursor===0){this.selected=id;this.result=null;}
    try{const value=await rpcProduct<Page>('legacy.history.read',{record_id:id,cursor,limit:30});if(!this.isConnected||epoch!==this.epoch)return;this.result={...value,messages:cursor?[...(this.result?.messages||[]),...value.messages]:value.messages};}
    catch(error){if(epoch===this.epoch)this.error=errorMessage(error);}
    finally{if(epoch===this.epoch)this.busy=false;}
  }
  private async archive(){
    const epoch=++this.epoch;this.busy=true;this.error='';
    try{await rpcProduct('legacy.archive');if(this.isConnected&&epoch===this.epoch)await this.reload();}
    catch(error){if(epoch===this.epoch){this.error=errorMessage(error);this.busy=false;}}
  }
  override render(){return renderSettingsSection({title:'旧会话档案',description:'只读查看已显式迁移的旧记录，保留原来源；不会冒充新框架会话或自动继续执行。归档后原目录可以脱离运行链。'},html`
    ${this.error?html`<div class="callout danger" role="alert" data-testid="mirally-legacy-error">${this.error}</div>`:nothing}
    ${renderSettingsRow({title:this.loaded?`${this.entries.length} 份旧记录`:'正在读取旧记录索引',control:html`<button class="btn btn--sm" data-testid="mirally-legacy-refresh" ?disabled=${this.busy} @click=${()=>void this.reload()}>刷新索引</button>`})}
    ${this.entries.some(entry=>!entry.archived)?renderSettingsRow({title:'将旧记录归档到 Mirally',description:'只复制已登记且内容指纹仍匹配的文件，不修改或删除原始记录。',control:html`<button class="btn btn--sm" data-testid="mirally-legacy-archive" ?disabled=${this.busy} @click=${()=>void this.archive()}>复制到新目录</button>`}):nothing}
    ${this.loaded&&!this.entries.length?renderSettingsEmpty('尚未显式迁移旧会话。原私有目录不会被自动扫描或导入。'):nothing}
    ${this.entries.length?renderSettingsRow({title:'选择只读记录',stacked:true,control:html`<select class="settings-input" data-testid="mirally-legacy-select" .value=${this.selected} ?disabled=${this.busy} @change=${(event:Event)=>void this.read((event.target as HTMLSelectElement).value)}><option value="">选择记录…</option>${this.entries.map(entry=>html`<option value=${entry.id}>${entry.session_id} · ${entry.archived?'已归档':'原始索引'} · ${Math.ceil(entry.bytes/1024)} KiB</option>`)}</select>`}):nothing}
    ${this.result?html`<div data-testid="mirally-legacy-messages">${this.result.messages.map(message=>html`<article class="settings-group" style="margin-block:12px;padding:16px"><strong>${message.role}</strong><pre style="white-space:pre-wrap;overflow-wrap:anywhere;font:inherit">${message.text}</pre>${message.truncated?html`<small>此条内容已按显示上限截短。</small>`:nothing}</article>`)}</div>${this.result.next_cursor!==null?html`<button class="btn btn--sm" data-testid="mirally-legacy-more" ?disabled=${this.busy} @click=${()=>void this.read(this.selected,this.result!.next_cursor!)}>读取下一页</button>`:nothing}`:nothing}
  `);}
}
