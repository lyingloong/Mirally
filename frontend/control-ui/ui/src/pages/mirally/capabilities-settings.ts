import {html,nothing} from 'lit';
import {customElement,state} from 'lit/decorators.js';
import {live} from 'lit/directives/live.js';
import {OpenClawLightDomElement} from '../../lit/openclaw-element.ts';
import {renderSettingsSection,renderSettingsRow,renderSettingsToggle} from '../../components/settings-ui.ts';
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
  private async reload(){const epoch=++this.epoch;this.busy=true;this.error='';try{const [c,list]=await Promise.all([rpcProduct<Config>('capabilities.get'),rpcProduct<{connections:{id:string;name:string}[]}>('model.connections.list')]);if(!this.isConnected||epoch!==this.epoch)return;this.config=c;this.connections=list.connections;this.paths=c.read_roots.join('\n');this.connection=c.search_connection;this.web=c.web_enabled;this.write=c.workspace_write_enabled;}catch(e){if(epoch===this.epoch)this.error=errorMessage(e);}finally{if(epoch===this.epoch)this.busy=false;}}
  private async save(){if(!this.config)return;this.busy=true;this.error='';this.notice='';try{await rpcProduct('capabilities.configure',{expected_revision:this.config.revision,patch:{read_roots:this.paths.split(/\r?\n/).map(p=>p.trim()).filter(Boolean),search_connection:this.connection.trim(),web_enabled:this.web,workspace_write_enabled:this.write}});await this.reload();this.notice='能力范围已保存。正在运行的旧请求会停止，后续请求使用新范围。';}catch(e){this.error=errorMessage(e);this.busy=false;}}
  override render(){return renderSettingsSection({title:'网页与文件能力',description:'工作区可读取。其他文件夹须由你明确授权；网页请求和工作区写入分别开启。'},html`
    ${this.error?html`<div class="callout danger" role="alert" data-testid="mirally-capabilities-error">${this.error}</div>`:nothing}
    ${this.notice?html`<div class="callout" role="status" data-testid="mirally-capabilities-notice">${this.notice}</div>`:nothing}
    ${renderSettingsRow({title:'Mirally 工作区',description:this.config?.workspace||'正在读取…'})}
    ${renderSettingsRow({title:'授权只读文件夹',description:'每行一个目录。留空仅允许读取 Mirally 工作区；不会扫描未授权的其他目录。',stacked:true,control:html`<textarea class="settings-input" rows="3" data-testid="mirally-capabilities-roots" .value=${live(this.paths)} ?disabled=${this.busy} @input=${(e:Event)=>this.paths=(e.target as HTMLTextAreaElement).value}></textarea>`})}
    ${renderSettingsRow({title:'允许工作区文字写入',description:'仍需在每次工作中选择保存文件。只能写入 Mirally 工作区，其他授权目录保持只读；覆盖前核对已读取内容指纹。',control:html`<span data-testid="mirally-capabilities-write">${renderSettingsToggle({checked:this.write,disabled:this.busy,ariaLabel:'允许工作区文字写入',onChange:value=>this.write=value})}</span>`})}
    ${renderSettingsRow({title:'允许网页研究',description:'允许模型发起网页搜索与正文读取。会向网页服务发送查询或URL，可能产生服务费用；网页内容作为外部资料处理。',control:html`<span data-testid="mirally-capabilities-web">${renderSettingsToggle({checked:this.web,disabled:this.busy,ariaLabel:'允许网页研究',onChange:value=>this.web=value})}</span>`})}
    ${renderSettingsRow({title:'网页服务连接',description:'内置搜索与网页正文读取使用所选 DeepSeek 官方连接。其他网页服务可通过 MCP/插件接入。',control:html`<select class="settings-input" data-testid="mirally-capabilities-connection" .value=${live(this.connection)} ?disabled=${this.busy} @change=${(e:Event)=>this.connection=(e.target as HTMLSelectElement).value}><option value="chat">跟随对话连接</option>${this.connections.map(item=>html`<option value=${item.id}>${item.name}</option>`)}</select>`})}
    ${renderSettingsRow({title:'保存访问范围',control:html`<button class="btn btn--sm" data-testid="mirally-capabilities-save" ?disabled=${this.busy||!this.config} @click=${()=>void this.save()}>保存能力设置</button>`})}
  `);}
}
