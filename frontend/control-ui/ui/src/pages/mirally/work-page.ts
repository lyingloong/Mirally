import {consume} from '@lit/context';
import {html} from 'lit';
import {customElement, state} from 'lit/decorators.js';
import {applicationContext,type ApplicationContext} from '../../app/context.ts';
import {pathForRoute} from '../../app-route-paths.ts';
import {OpenClawLightDomElement} from '../../lit/openclaw-element.ts';
import {renderSettingsPageHeader} from '../../components/settings-ui.ts';
import {sessionNavigationTarget} from '../../lib/sessions/route-navigation.ts';
import {rpcProduct} from './api.ts';
import './work-panel.ts';
const workFromSearch=(search:string)=>{
  const value=new URLSearchParams(search).get('work')||'';
  return /^[A-Za-z0-9_-]{1,128}$/.test(value)?value:'';
};
@customElement('mirally-work-page')
export class MirallyWorkPage extends OpenClawLightDomElement {
  @consume({context:applicationContext,subscribe:true}) private context!:ApplicationContext;
  @state() private selectedWork = workFromSearch(location.search);
  @state() private initialFilter = new URLSearchParams(location.search).get('filter') === 'all' ? 'all' : 'current';
  private stopRoute?: () => void;
  private routeOwner?: ApplicationContext['router'];
  private syncRoute() {
    const location=this.context?.router.getState().location;
    if(!location||location.pathname.replace(/\/$/,'')!==pathForRoute('tasks',this.context.basePath).replace(/\/$/,''))return;
    this.selectedWork=workFromSearch(location.search);
    this.initialFilter=new URLSearchParams(location.search).get('filter') === 'all' ? 'all' : 'current';
  }
  protected override updated() {
    const router=this.context?.router;
    if(!router||router===this.routeOwner)return;
    this.stopRoute?.();this.routeOwner=router;
    this.stopRoute=router.subscribe(()=>this.syncRoute());this.syncRoute();
  }
  override disconnectedCallback() {this.stopRoute?.();this.stopRoute=undefined;this.routeOwner=undefined;super.disconnectedCallback();}
  private readonly workSelected=(event:CustomEvent<{work_id:string}>)=>{
    const id=event.detail?.work_id||'';
    this.selectedWork=id;
    const params=new URLSearchParams(this.context.router.getState().location.search);
    if(id)params.set('work',id);else params.delete('work');
    const search=params.toString();this.context.replace('tasks',{search:search?'?'+search:''});
  };
  private readonly openConversation=(id:string)=>{
    const target=sessionNavigationTarget({context:this.context,face:'chat',sessionKey:'agent:main:'+id});
    this.context.navigate('chat',target.options);
  };
  private readonly openPreparation = async () => {
    // The retained preparation surface is anchored to a chat pane. Navigate to
    // the existing new-conversation route first, then let its own handoff open
    // the work surface once the draft controller is ready.
    this.context.navigate('new-session',{search:''});
    const deadline=performance.now()+10000;
    while(performance.now()<deadline) {
      const page=document.querySelector('openclaw-new-session-page') as (Element & {submission?:unknown})|null;
      if(page?.isConnected&&page.submission) {
        await (window as Window & {__mirallyWork?:{open:()=>Promise<void>}}).__mirallyWork?.open();
        return;
      }
      await new Promise<void>(resolve=>requestAnimationFrame(()=>resolve()));
    }
  };
  override render(){return html`${renderSettingsPageHeader({title:'工作'})}<div class="mirally-work-desktop"><mirally-work-panel desktop .rpc=${rpcProduct} .selected=${this.selectedWork} .initialFilter=${this.initialFilter} .openConversation=${this.openConversation} .openPreparation=${(window as Window & {__mirallyWork?:unknown}).__mirallyWork?this.openPreparation:undefined} @work-selected=${this.workSelected}></mirally-work-panel></div>`;}
}
