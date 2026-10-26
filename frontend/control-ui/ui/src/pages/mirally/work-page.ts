import {consume} from '@lit/context';
import {html} from 'lit';
import {customElement} from 'lit/decorators.js';
import {applicationContext,type ApplicationContext} from '../../app/context.ts';
import {OpenClawLightDomElement} from '../../lit/openclaw-element.ts';
import {renderSettingsPageHeader} from '../../components/settings-ui.ts';
import {sessionNavigationTarget} from '../../lib/sessions/route-navigation.ts';
import {rpcProduct} from './api.ts';
import './work-panel.ts';
@customElement('mirally-work-page')
export class MirallyWorkPage extends OpenClawLightDomElement {
  @consume({context:applicationContext,subscribe:true}) private context!:ApplicationContext;
  private readonly openConversation=(id:string)=>{
    const target=sessionNavigationTarget({context:this.context,face:'chat',sessionKey:'agent:main:'+id});
    this.context.navigate('chat',target.options);
  };
  override render(){return html`${renderSettingsPageHeader({title:'工作',subtitle:'关注实际进展、继续交代，并核对电脑上的每一次运行。'})}<div class="mirally-work-desktop"><mirally-work-panel .rpc=${rpcProduct} .openConversation=${this.openConversation}></mirally-work-panel></div>`;}
}
