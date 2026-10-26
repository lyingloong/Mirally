import '@awesome.me/webawesome/dist/components/qr-code/qr-code.js';
import {html,nothing} from 'lit';
import {customElement,state} from 'lit/decorators.js';
import {live} from 'lit/directives/live.js';
import {OpenClawLightDomElement} from '../../lit/openclaw-element.ts';
import {renderSettingsRow,renderSettingsSection,renderSettingsStatus} from '../../components/settings-ui.ts';
import {rpcProduct} from './api.ts';
import {timeLabel} from '../../mobile/api.ts';
type Data=Record<string,any>;
/** Desktop-only enrollment owner. Phone bearer tokens are never returned here. */
@customElement('mirally-mobile-settings')
export class MirallyMobileSettings extends OpenClawLightDomElement {
  @state() private status:Data|null=null;
  @state() private pairing:Data|null=null;
  @state() private url='';
  @state() private name='我的手机';
  @state() private busy=false;
  @state() private error='';
  @state() private notice='';
  @state() private revokeId='';
  private dirty=false;
  private epoch=0;
  private timer:ReturnType<typeof setTimeout>|undefined;
  private readonly ready=()=>void this.refresh();
  private readonly visibility=()=>{this.clearPoll();if(!document.hidden)void this.refresh();};
  override connectedCallback(){super.connectedCallback();window.addEventListener('mirally-host-ready',this.ready);document.addEventListener('visibilitychange',this.visibility);void this.refresh();}
  override disconnectedCallback(){++this.epoch;this.clearPoll();window.removeEventListener('mirally-host-ready',this.ready);document.removeEventListener('visibilitychange',this.visibility);super.disconnectedCallback();}
  private clearPoll(){if(this.timer!==undefined)clearTimeout(this.timer);this.timer=undefined;}
  private schedule(){this.clearPoll();if(this.isConnected&&!document.hidden)this.timer=setTimeout(()=>void this.refresh(),5000);}
  private async refresh(){if(this.busy||!this.isConnected||document.hidden){this.schedule();return;}const epoch=++this.epoch;
    try{const result=await rpcProduct<Data>('mobile.status');if(epoch!==this.epoch||!this.isConnected)return;this.status=result;if(!this.dirty)this.url=result.external_url||'';this.error='';}
    catch{if(epoch===this.epoch&&this.isConnected)this.error='暂时无法读取手机接入状态，请稍后刷新。';}
    finally{if(epoch===this.epoch&&this.isConnected)this.schedule();}}
  private async action(action:()=>Promise<unknown>,notice=''){if(this.busy)return;this.busy=true;this.error='';this.notice='';this.clearPoll();const epoch=++this.epoch;
    try{await action();if(epoch===this.epoch&&this.isConnected)this.notice=notice;}
    catch(error){if(epoch===this.epoch&&this.isConnected)this.error=error instanceof Error?error.message:'操作没有完成，请刷新后重试。';}
    finally{if(epoch===this.epoch&&this.isConnected){this.busy=false;void this.refresh();}}}
  override render(){const status=this.status;return html`
    ${renderSettingsSection({title:'手机接入',description:'在手机网页查看工作、会话和成果，继续指挥这台电脑。与微信文字通道分别配对。'},html`
      ${this.error?html`<p class="callout danger" role="alert">${this.error}</p>`:nothing}${this.notice?html`<p class="callout" role="status">${this.notice}</p>`:nothing}
      ${renderSettingsRow({title:'手机服务',control:renderSettingsStatus({kind:status?.enabled&&!status?.error?'ok':'muted',label:status?.error?'启动异常':status?.enabled?'已启用':'未启用'})})}
      ${status?.error?renderSettingsRow({title:'连接提示',description:'手机服务暂未启动。检查端口配置，保存后重试；电脑端和微信仍可使用。'}):nothing}
      ${renderSettingsRow({title:'启用手机接入',description:'只启动独立的受限手机服务。电脑原有管理接口不会因此开放。',control:html`<input type="checkbox" aria-label="启用手机接入" .checked=${status?.enabled===true} ?disabled=${this.busy||!status} @change=${(e:Event)=>this.action(()=>rpcProduct('mobile.configure',{enabled:(e.target as HTMLInputElement).checked,external_url:status?.external_url||''}),'手机接入配置已更新。')}>`})}
      ${renderSettingsRow({title:'私人 HTTPS 地址',description:'先使用已有私人网络的 HTTPS 转发（如 Tailscale Serve），目标设为下方本机地址，再填写手机可访问的地址。此处不会安装转发服务或开放公网端口。',stackedOnNarrow:true,control:html`<form class="mirally-product-actions" @submit=${(e:SubmitEvent)=>{e.preventDefault();void this.action(async()=>{await rpcProduct('mobile.configure',{enabled:status?.enabled===true,external_url:this.url.trim()});this.dirty=false;this.pairing=null;},'接入地址已保存。');}}><input class="settings-input" aria-label="手机私人HTTPS地址" type="url" placeholder="https://电脑名.私人域名" .value=${live(this.url)} @input=${(e:Event)=>{this.url=(e.target as HTMLInputElement).value;this.dirty=true;}} ?disabled=${this.busy}><button class="btn" type="submit" ?disabled=${this.busy||!status}>保存地址</button></form>`})}
      ${status?.url||status?.local_url?renderSettingsRow({title:'本机手机服务地址',description:'仅供本机预览及私人 HTTPS 转发的目标地址；手机无法通过自己的 127.0.0.1 连接这台电脑。',control:html`<code>${status.local_url||status.url}</code>`,stackedOnNarrow:true}):nothing}
      ${status?.enabled?renderSettingsRow({title:'配对一台手机',description:status.external_url?'生成一次性短期配对码，用手机扫描或在页面输入。请勿将二维码、配对码发给他人。':'尚未设置私人 HTTPS 地址。可以生成配对码在本机预览；远程手机需要先完成上述网络设置。',stackedOnNarrow:true,
        control:html`<div class="mirally-product-actions"><input class="settings-input" aria-label="配对设备名称" maxlength="80" .value=${live(this.name)} @input=${(e:Event)=>this.name=(e.target as HTMLInputElement).value} ?disabled=${this.busy}><button class="btn primary" data-testid="mobile-pair-create" ?disabled=${this.busy} @click=${()=>this.action(async()=>{this.pairing=await rpcProduct<Data>('mobile.pair.create',{device_name:this.name.trim()||'我的手机'});})}>生成配对码</button></div>`}):nothing}
      ${this.pairing?html`<div class="mirally-product-form"><p class="mirally-mobile-pair-code" data-testid="mobile-pair-code">${this.pairing.code}</p><p class="mirally-product-meta">有效至 ${timeLabel(this.pairing.expires_at)}。每个配对码只可使用一次。</p>
        ${this.pairing.qr_content||this.pairing.url?html`<div class="mirally-weixin-qr"><wa-qr-code .value=${this.pairing.qr_content||this.pairing.url} label="手机接入配对二维码" size="200" fill="#111111" background="#ffffff"></wa-qr-code></div><a href=${this.pairing.url} target="_blank" rel="noopener noreferrer">打开手机页面（本机预览）</a>`:nothing}
      </div>`:nothing}
    `)}
    ${renderSettingsSection({title:'已配对设备',description:'撤销后，该设备不能继续查看或发起操作。已有会话与工作保留；已批准运行需在工作详情另行停止。'},html`
      ${(status?.devices||[]).length?(status!.devices as Data[]).map(device=>html`<article class="mirally-mobile-device"><strong>${device.name||device.device_name||'手机'}</strong><p class="mirally-product-meta">最近使用 ${timeLabel(device.last_seen||device.last_seen_at||device.updated_at||device.created_at)}${device.revoked||device.revoked_at?' · 已撤销':''}</p>
        ${!(device.revoked||device.revoked_at)?(this.revokeId===device.id?html`<div class="mirally-product-actions"><button class="btn danger" ?disabled=${this.busy} @click=${()=>this.action(async()=>{await rpcProduct('mobile.devices.revoke',{device_id:device.id});this.revokeId='';},'设备连接已撤销。')}>确认撤销</button><button class="btn" ?disabled=${this.busy} @click=${()=>this.revokeId=''}>取消</button></div>`:html`<button class="btn" ?disabled=${this.busy} @click=${()=>this.revokeId=device.id}>撤销连接</button>`):nothing}</article>`):html`<p class="mirally-product-empty">还没有配对的手机。</p>`}
    `)}`;}
}
