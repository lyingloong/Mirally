import {html, nothing} from 'lit';
import {customElement, state} from 'lit/decorators.js';
import {live} from 'lit/directives/live.js';
import {OpenClawLightDomElement} from '../../lit/openclaw-element.ts';
import {renderSettingsSection, renderSettingsRow} from '../../components/settings-ui.ts';
import {rpcProduct, errorMessage} from './api.ts';

type SpeechSettings = {provider:string; base_url:string; model:string; language:string; local_model_path:string;
  api_key_configured:boolean; local_ready:boolean; local_models:{name:string;path:string}[]};

@customElement('mirally-speech-settings')
export class MirallySpeechSettings extends OpenClawLightDomElement {
  @state() private config:SpeechSettings|null=null;
  @state() private provider='none';
  @state() private url='';
  @state() private model='';
  @state() private language='auto';
  @state() private path='';
  @state() private key='';
  @state() private clearKey=false;
  @state() private busy=false;
  @state() private error='';
  @state() private notice='';
  private epoch=0;
  override connectedCallback(){super.connectedCallback();void this.load();}
  override disconnectedCallback(){++this.epoch;this.key='';super.disconnectedCallback();}
  private async load(){
    const epoch=++this.epoch;this.busy=true;
    try {const c=await rpcProduct<SpeechSettings>('speech.settings');if(!this.isConnected||epoch!==this.epoch)return;
      this.config=c;this.provider=c.provider;this.url=c.base_url;this.model=c.model;this.language=c.language;this.path=c.local_model_path;
    }catch(e){if(epoch===this.epoch)this.error=errorMessage(e);}finally{if(epoch===this.epoch)this.busy=false;}
  }
  private async save(){
    this.busy=true;this.error='';this.notice='';
    try {const patch:Record<string,unknown>={provider:this.provider,base_url:this.url,model:this.model,language:this.language,local_model_path:this.path};
      if(this.key)patch.api_key=this.key;
      if(this.clearKey && this.provider!=='openai_compatible')patch.clear_api_key=true;
      await rpcProduct('speech.configure',{patch});this.key='';this.clearKey=false;await this.load();this.notice='语音设置已保存。';
    }catch(e){this.error=errorMessage(e);this.busy=false;}
  }
  override render(){return renderSettingsSection({title:'语音识别'},html`
    ${this.error?html`<div role="alert" class="callout danger">${this.error}</div>`:nothing}
    ${this.notice?html`<div role="status" class="callout">${this.notice}</div>`:nothing}
    ${renderSettingsRow({title:'识别服务',control:html`<select class="settings-input" aria-label="识别服务" .value=${live(this.provider)} ?disabled=${this.busy} @change=${(e:Event)=>this.provider=(e.target as HTMLSelectElement).value}>
      <option value="none">关闭</option><option value="openai_compatible">兼容 OpenAI 的语音服务</option><option value="faster_whisper">本地 Whisper</option></select>`})}
    ${this.provider==='openai_compatible'?html`
      ${renderSettingsRow({title:'服务地址',control:html`<input class="settings-input" aria-label="语音服务地址" .value=${live(this.url)} @input=${(e:Event)=>this.url=(e.target as HTMLInputElement).value}>`})}
      ${renderSettingsRow({title:'语音模型',control:html`<input class="settings-input" aria-label="语音模型" .value=${live(this.model)} @input=${(e:Event)=>this.model=(e.target as HTMLInputElement).value}>`})}
      ${renderSettingsRow({title:'API 密钥',control:html`<input class="settings-input" type="password" autocomplete="new-password" aria-label="语音 API 密钥" placeholder=${this.config?.api_key_configured?'已保存，留空保留':''} .value=${live(this.key)} @input=${(e:Event)=>this.key=(e.target as HTMLInputElement).value}>`})}
    `:nothing}
    ${this.provider==='faster_whisper'?html`
      ${renderSettingsRow({title:'本地模型目录',description:this.config?.local_ready?undefined:'请先安装 faster-whisper 并准备本地模型。',control:html`<input class="settings-input" aria-label="本地语音模型目录" list="mirally-speech-models" .value=${live(this.path)} @input=${(e:Event)=>this.path=(e.target as HTMLInputElement).value}><datalist id="mirally-speech-models">${(this.config?.local_models??[]).map(item=>html`<option value=${item.path}>${item.name}</option>`)}</datalist>`})}
    `:nothing}
    ${this.provider!=='openai_compatible' && this.config?.api_key_configured ? renderSettingsRow({title:'已保存的 API 密钥',control:html`<label><input type="checkbox" .checked=${this.clearKey} @change=${(e:Event)=>this.clearKey=(e.target as HTMLInputElement).checked}> 删除密钥</label>`}) : nothing}
    ${this.provider!=='none'?renderSettingsRow({title:'识别语言',control:html`<input class="settings-input" aria-label="识别语言" .value=${live(this.language)} @input=${(e:Event)=>this.language=(e.target as HTMLInputElement).value}>`}):nothing}
    ${renderSettingsRow({title:'保存语音设置',control:html`<button class="btn btn--sm" ?disabled=${this.busy||!this.config} @click=${()=>void this.save()}>保存</button>`})}
  `);}
}
