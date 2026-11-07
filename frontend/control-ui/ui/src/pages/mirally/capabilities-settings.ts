import {html,nothing} from 'lit';
import {customElement,state} from 'lit/decorators.js';
import {live} from 'lit/directives/live.js';
import {OpenClawLightDomElement} from '../../lit/openclaw-element.ts';
import {renderSettingsSection,renderSettingsRow} from '../../components/settings-ui.ts';
import {rpcProduct,errorMessage} from './api.ts';

type Config={revision:number;read_roots:string[];web_enabled:boolean;workspace_write_enabled:boolean;search_connection:string;workspace:string};
@customElement('mirally-capabilities-settings')
export class MirallyCapabilitiesSettings extends OpenClawLightDomElement{
  @state() private config:Config|null=null;
  @state() private paths='';
  @state() private connection='chat';
  @state() private web=false;
  @state() private write=false;
  @state() private error='';
  @state() private notice='';
  @state() private busy=false;
  @state() private connections:{id:string;name:string}[]=[];
  private epoch=0;
  override connectedCallback(){super.connectedCallback();void this.reload();}
  override disconnectedCallback(){++this.epoch;super.disconnectedCallback();}
  private async reload() {
    const epoch=++this.epoch;
    this.busy=true;this.error='';
    try {
      const [config,list]=await Promise.all([rpcProduct<Config>('capabilities.get'),rpcProduct<{connections:{id:string;name:string}[]}>('model.connections.list')]);
      if(!this.isConnected||epoch!==this.epoch)return false;
      this.config=config;this.connections=list.connections;this.paths=config.read_roots.join('\n');
      this.connection=config.search_connection;this.web=config.web_enabled;this.write=config.workspace_write_enabled;
      return true;
    } catch(error) {
      if(this.isConnected&&epoch===this.epoch)this.error=errorMessage(error);
      return false;
    } finally {
      if(this.isConnected&&epoch===this.epoch)this.busy=false;
    }
  }
  private async save() {
    if(!this.config||this.busy)return;
    const epoch=++this.epoch;
    this.busy=true;this.error='';this.notice='';
    try {
      await rpcProduct('capabilities.configure',{expected_revision:this.config.revision,patch:{
        read_roots:this.paths.split(/\r?\n/).map(path=>path.trim()).filter(Boolean),
        search_connection:this.connection.trim(),web_enabled:this.web,workspace_write_enabled:this.write,
      }});
      if(!this.isConnected||epoch!==this.epoch)return;
      if(await this.reload())this.notice='能力范围已保存。';
    } catch(error) {
      if(this.isConnected&&epoch===this.epoch){this.error=errorMessage(error);this.busy=false;}
    }
  }
  override render() {
    return renderSettingsSection({title:'网页与文件能力'},html`
      ${this.error?html`<div class="callout danger" role="alert" data-testid="mirally-capabilities-error">${this.error}<button class="btn btn--sm" type="button" ?disabled=${this.busy} @click=${()=>void this.reload()}>重新读取</button></div>`:nothing}
      ${this.notice?html`<div class="callout" role="status" data-testid="mirally-capabilities-notice">${this.notice}</div>`:nothing}
      ${!this.config?html`<p class="mirally-product-empty" role="status">${this.busy?'正在读取访问范围…':'尚未读取访问范围。'}</p>`:html`
        ${renderSettingsRow({title:'Mirally 工作区',description:this.config.workspace})}
        ${renderSettingsRow({title:'授权只读文件夹',description:'每行一个目录；留空仅允许读取 Mirally 工作区。',stacked:true,control:html`<textarea class="settings-input" aria-label="授权只读文件夹" rows="3" data-testid="mirally-capabilities-roots" .value=${live(this.paths)} ?disabled=${this.busy} @input=${(event:Event)=>this.paths=(event.target as HTMLTextAreaElement).value}></textarea>`})}
        ${renderSettingsRow({title:'工作区文字写入',description:'仍需在每次工作中选择保存文件；其他授权目录保持只读。',control:html`<label class="mirally-product-checkbox" data-testid="mirally-capabilities-write"><input type="checkbox" aria-label="允许工作区文字写入" .checked=${live(this.write)} ?disabled=${this.busy} @change=${(event:Event)=>this.write=(event.target as HTMLInputElement).checked}>允许写入</label>`})}
        ${renderSettingsRow({title:'网页研究',description:'搜索与读取会向网页服务发送查询或网址，可能产生费用。',control:html`<label class="mirally-product-checkbox" data-testid="mirally-capabilities-web"><input type="checkbox" aria-label="允许网页研究" .checked=${live(this.web)} ?disabled=${this.busy} @change=${(event:Event)=>this.web=(event.target as HTMLInputElement).checked}>允许联网</label>`})}
        ${renderSettingsRow({title:'网页服务连接',description:'内置搜索与正文读取需要 DeepSeek 官方连接。',control:html`<select class="settings-input" aria-label="网页服务连接" data-testid="mirally-capabilities-connection" .value=${live(this.connection)} ?disabled=${this.busy} @change=${(event:Event)=>this.connection=(event.target as HTMLSelectElement).value}><option value="chat" .selected=${this.connection === 'chat'}>跟随对话连接</option>${this.connections.map(item=>html`<option value=${item.id} .selected=${item.id === this.connection}>${item.name}</option>`)}</select>`})}
        ${renderSettingsRow({title:'保存访问范围',description:'保存会停止正在运行的旧请求，后续请求使用新范围。',control:html`<button class="btn primary" type="button" data-testid="mirally-capabilities-save" ?disabled=${this.busy} @click=${()=>void this.save()}>保存能力设置</button>`})}
      `}
    `);
  }
}
