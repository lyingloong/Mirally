import { definePage, redirect } from '@openclaw/uirouter';
import { html, nothing } from 'lit';
import { APP_ROUTE_IDS, pathForRoute, routePageSpec, type RouteId } from '../../app-route-paths.ts';
import type { ApplicationContext } from '../../app/context.ts';

// Route IDs remain stable for the retained shell. Product pages own their
// contents and use the existing authenticated Mirally client, without loaders
// that assume an OpenClaw Gateway, node, channel or administrator service.
const settings = {config:'general',profile:'profile','model-providers':'model','model-setup':'model',notifications:'initiative',about:'about'} as const;
const ownIds: RouteId[] = [...Object.keys(settings) as RouteId[], 'custodian','cron','memory','activity','channels','tasks'];
const ownPages = ownIds.map(id=>definePage({
  ...routePageSpec(id),
  component: async()=>{
    if(id==='activity')return {header:true,render:()=>nothing}; // Existing Mirally Activity panel owns this route.
    if(id==='tasks') {await import('./work-page.ts');return {header:true,render:()=>html`<mirally-work-page></mirally-work-page>`};}
    if(id==='channels') {await import('./channels-page.ts');return {header:true,render:()=>html`<mirally-channels-page></mirally-channels-page>`};}
    if(id==='memory') {await import('./memory-page.ts');return {header:true,render:()=>html`<mirally-memory-page></mirally-memory-page>`};}
    if(id==='custodian'||id==='cron') {await import('./messages-page.ts');return {header:true,render:()=>html`<mirally-messages-page .page=${id==='cron'?'reminders':'inbox'}></mirally-messages-page>`};}
    await import('./settings-page.ts');return {header:true,render:()=>html`<mirally-settings-page .page=${settings[id as keyof typeof settings]}></mirally-settings-page>`};
  },
}));
const extensionsPage=definePage({...routePageSpec('plugins'),component:async()=>{
  await import('./extensions-page.ts');return {header:true,render:()=>html`<mirally-extensions-page></mirally-extensions-page>`};
}});
const redirects: Partial<Record<RouteId,RouteId>> = {
  settings:'config', connection:'model-providers', agents:'profile','ai-agents':'profile',
  automation:'cron',skills:'plugins','skill-settings':'plugins','plugin-settings':'plugins',mcp:'plugins','skill-workshop':'plugins',plugin:'plugins',
  'agents-home':'new-session',dashboards:'new-session',sessions:'new-session',
};
const retained=new Set<RouteId>(['chat','dashboard','new-session','appearance',...ownIds,'plugins']);
const compatibilityPages=APP_ROUTE_IDS.filter(id=>!retained.has(id)).map(id=>definePage({
  ...routePageSpec(id),
  loader:(context:ApplicationContext)=>redirect({pathname:pathForRoute(redirects[id]||'about',context.basePath),search:'',hash:''}),
  component:async()=>({render:()=>nothing}),
}));
export const pages=[...ownPages,extensionsPage,...compatibilityPages];
