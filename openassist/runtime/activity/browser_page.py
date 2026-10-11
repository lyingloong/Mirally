"""Developer-facing read-only browser for every Activity data layer.

The HTTP handler owns authentication and the endpoint implementation.  This
module only contains the page so the monitor route can serve a small static
asset without introducing a frontend build step.
"""

from __future__ import annotations


BROWSER_HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mirally Activity Browser</title>
<style>
:root { color-scheme: light; font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;
  --ink:#162635; --muted:#66788a; --line:#d8e1e8; --paper:#f4f7f9; --surface:#fff;
  --blue:#076f9c; --blue-bg:#e7f4fa; --good:#18794e; --warn:#a45a00; --bad:#b42318; }
* { box-sizing:border-box; } body { margin:0; background:var(--paper); color:var(--ink); }
button,input,select { font:inherit; } button { color:var(--ink); background:var(--surface); border:1px solid #b7c7d2; border-radius:4px; padding:6px 10px; cursor:pointer; }
button:hover,button[aria-selected="true"] { border-color:var(--blue); color:var(--blue); background:var(--blue-bg); }
button:disabled { opacity:.5; cursor:default; } header { position:sticky; z-index:3; top:0; background:var(--surface); border-bottom:1px solid var(--line); padding:12px 20px; display:flex; align-items:center; gap:12px; }
h1 { font-size:18px; margin:0; } header small { color:var(--muted); } .header-actions { margin-left:auto; display:flex; gap:7px; align-items:center; flex-wrap:wrap; }
main { max-width:1600px; margin:auto; padding:16px 20px 36px; } .notice { display:none; border-left:3px solid var(--warn); background:#fff7e8; padding:9px 12px; margin-bottom:12px; overflow-wrap:anywhere; }
.filters { background:var(--surface); border:1px solid var(--line); padding:10px 12px; display:flex; gap:8px; align-items:end; flex-wrap:wrap; margin-bottom:12px; }
.filters label { display:grid; gap:3px; color:var(--muted); font-size:12px; } .filters input,.filters select { min-width:130px; border:1px solid var(--line); padding:6px 8px; color:var(--ink); background:#fff; }
.filters .wide { min-width:240px; } .filters .grow { flex:1 1 240px; } .filters .toggle { display:flex; align-items:center; gap:5px; height:32px; }
.segmented { display:flex; flex-wrap:wrap; gap:0; align-items:end; border:0; padding:0; margin:0; } .segmented legend { color:var(--muted); font-size:12px; padding:0 0 3px; } .segmented button { border-radius:0; padding:6px 9px; } .segmented button:first-of-type { border-radius:4px 0 0 4px; } .segmented button:last-of-type { border-radius:0 4px 4px 0; margin-left:-1px; } .segmented button[aria-pressed="true"] { color:var(--blue); background:var(--blue-bg); border-color:var(--blue); z-index:1; }
.tabs { display:flex; gap:5px; flex-wrap:wrap; margin-bottom:12px; } .tabs button { padding:7px 11px; }
.layout { display:grid; grid-template-columns:minmax(0,1.45fr) minmax(300px,.85fr); gap:14px; align-items:start; } .layout>* { min-width:0; }
section { background:var(--surface); border-top:2px solid var(--line); min-width:0; } section h2 { font-size:14px; margin:0; padding:10px 12px; border-bottom:1px solid var(--line); }
.toolbar { min-height:42px; border-bottom:1px solid var(--line); display:flex; align-items:center; gap:8px; padding:7px 10px; color:var(--muted); } .toolbar .spacer { flex:1; }
.table-wrap { overflow:auto; max-height:calc(100vh - 300px); min-height:280px; } table { border-collapse:collapse; width:100%; table-layout:fixed; } th,td { text-align:left; vertical-align:top; border-bottom:1px solid #edf1f4; padding:8px 10px; overflow-wrap:anywhere; } th { position:sticky; top:0; background:var(--surface); color:var(--muted); font-size:12px; z-index:1; }
tbody tr[data-id] { cursor:pointer; } tbody tr[data-id]:hover { background:#f1f7fa; } td time { color:var(--muted); white-space:nowrap; } .kind { font-weight:600; } .muted { color:var(--muted); } .ok { color:var(--good); } .warn { color:var(--warn); } .bad { color:var(--bad); }
.detail { min-height:320px; max-height:calc(100vh - 250px); overflow:auto; padding:12px; background:#fbfcfd; } pre { margin:0; white-space:pre-wrap; overflow-wrap:anywhere; font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace; }
.detail-tools { display:flex; gap:6px; align-items:center; flex-wrap:wrap; padding:8px 10px; border-bottom:1px solid var(--line); } .detail-tools .spacer { flex:1; }
.asset { display:none; max-width:100%; max-height:330px; object-fit:contain; margin:0 0 12px; background:#edf1f3; border:1px solid var(--line); } .refs { display:flex; flex-wrap:wrap; gap:5px; margin-bottom:10px; } .ref { border:1px solid var(--line); background:#fff; color:var(--blue); padding:3px 7px; border-radius:3px; cursor:pointer; font:12px ui-monospace,monospace; }
.pager { display:flex; align-items:center; gap:8px; padding:8px 10px; border-top:1px solid var(--line); color:var(--muted); } .pager .spacer { flex:1; } .pill { border:1px solid var(--line); border-radius:10px; padding:2px 7px; font-size:11px; color:var(--muted); }
.empty { color:var(--muted); text-align:center; padding:28px; } .summary { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:0; padding:8px 10px; border-bottom:1px solid var(--line); } .stat { border-right:1px solid var(--line); padding:5px 9px; min-height:45px; } .stat:last-child { border-right:0; } .stat span { color:var(--muted); font-size:12px; display:block; } .stat b { display:block; margin-top:2px; overflow-wrap:anywhere; }
@media (max-width:900px) { .layout { grid-template-columns:1fr; } .detail { max-height:55vh; } .table-wrap { max-height:55vh; } }
@media (max-width:560px) { header { padding:10px 12px; flex-wrap:wrap; } header small { flex:1 1 100%; order:3; } .header-actions { margin-left:0; } main { padding:12px; } .summary { grid-template-columns:repeat(2,minmax(0,1fr)); } .filters input,.filters select { min-width:0; width:100%; } .filters label { flex:1 1 140px; } }
</style>
</head>
<body>
<header><h1>Activity 数据浏览</h1><small id="endpoint">只读开发者视图 · loopback</small><div class="header-actions"><label class="toggle"><input id="reveal" type="checkbox" checked> 显示正文</label><button id="refresh">刷新</button></div></header>
<main>
<div id="notice" class="notice"></div>
<div class="filters">
  <label class="grow">搜索<input id="q" class="wide" placeholder="标题、URL、UIA、OCR、输入文本"></label>
  <label>开始时间<input id="start" type="datetime-local"></label>
  <label>结束时间<input id="end" type="datetime-local"></label>
  <label>应用<input id="app" placeholder="app id"></label>
  <label>类型<input id="kind" placeholder="kind"></label>
  <label>每页<select id="limit"><option>25</option><option selected>50</option><option>100</option><option>200</option></select></label>
  <fieldset id="mode-switch" class="segmented"><legend>上下文</legend><button type="button" data-mode="outline" aria-pressed="true">Outline</button><button type="button" data-mode="structured" aria-pressed="false">Structured</button><button type="button" data-mode="evidence" aria-pressed="false">Evidence</button></fieldset>
  <button id="apply">查询</button>
</div>
<nav class="tabs" aria-label="数据层">
  <button data-tab="raw" aria-selected="true">Raw Observation</button><button data-tab="items">Activity Items</button>
  <button data-tab="episodes">Episodes</button><button data-tab="evidence">Evidence</button><button data-tab="events">Events</button>
  <button data-tab="timeline">Timeline</button><button data-tab="context">Agent Context</button><button data-tab="summary">Summary</button>
  <button data-tab="candidates">Memory Candidates</button><button data-tab="facts">Memory Facts</button><button data-tab="diagnostics">Diagnostics</button>
</nav>
<div class="layout"><section>
  <h2 id="list-title">Raw Observation</h2><div id="summary" class="summary"></div>
  <div class="toolbar"><span id="data-status" class="pill">尚未查询</span><span class="spacer"></span><span id="count" class="muted"></span></div>
  <div class="table-wrap"><table><thead id="thead"></thead><tbody id="tbody"></tbody></table></div>
  <div class="pager"><button id="prev" disabled>上一页</button><button id="next" disabled>下一页</button><span class="spacer"></span><span id="page-label">第 1 页</span></div>
</section><section>
  <h2>详情 / 证据链</h2><div class="detail-tools"><button id="pretty">正文</button><button id="json">JSON</button><button id="download">下载 JSON</button><span class="spacer"></span><span id="detail-label" class="muted"></span></div>
  <div class="detail"><div id="refs" class="refs"></div><img id="asset" class="asset" alt="Activity asset"><pre id="detail"></pre></div>
</section></div>
</main>
<script>
const token=__TOKEN__, base=""; let tab="raw", page=1, cursor="", cursors=[""], nextCursor="", selected=null, jsonMode=false, loading=false, assetUrl=null, assetVersion=0, contextMode="outline", loadVersion=0;
const $=id=>document.getElementById(id); const esc=v=>String(v??"");
function html(v){return esc(v).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));}
function api(path, options={}){const headers=Object.assign({Authorization:"Bearer "+token},options.headers||{});return fetch(base+path,Object.assign({},options,{headers})).then(async r=>{const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||r.statusText);return d;});}
function query(){const p=new URLSearchParams();const values={q:"q",start:"start",end:"end",app:"app",kind:"kind",limit:"limit"};for(const [id,key] of Object.entries(values)){const control=$(id),value=control.value.trim();if(value&&!control.disabled)p.set(key,id.match(/^(start|end)$/)?new Date(value).getTime():value);}p.set("tokens","100000");p.set("episodes","100");if(tab==="context")p.set("mode",contextMode);const content=$("reveal").checked?"1":"0";p.set("include_uia",content);p.set("include_ocr",content);p.set("include_assets",content);p.set("include_content",content);return p;}
function when(ms){if(ms==null||ms==="")return "-";try{return new Date(Number(ms)).toLocaleString();}catch{return String(ms);}}
function idOf(v){return v?.id||v?.item_id||v?.episode_id||v?.evidence_id||v?.observation_id||v?.candidate_id||v?.fact_id||v?.event_id||v?.asset_id||v?.source_observation_id||"";}
function listOf(data){if(!data||typeof data!=="object")return[];if(tab==="events"){const groups=[["events",data.events||[]],["background",data.background_evidence||[]],["unattributed",data.unattributed_evidence||[]],["collection",data.collection_status||[]]];return groups.flatMap(([group,rows])=>rows.map(row=>({...row,_event_group:group})));}for(const key of [tab,"observations","items","episodes","evidence","events","timeline","candidates","facts","diagnostics","rows"]){if(Array.isArray(data[key]))return data[key];}if(Array.isArray(data.data))return data.data;if(tab==="summary"||tab==="diagnostics")return Object.entries(data).map(([key,value])=>({id:key,key,value}));return[];}
function titleFor(value){return ({raw:"Raw Observation",items:"Activity Items",episodes:"Episodes",evidence:"Evidence",events:"Events",timeline:"Timeline",context:"Agent Context",summary:"Summary",candidates:"Memory Candidates",facts:"Memory Facts",diagnostics:"Diagnostics"})[value]||value;}
function fieldsFor(item){if(!item||typeof item!=="object")return[];const fields=[];const id=idOf(item);if(id)fields.push(["ID",id]);const time=item.observed_at_utc_ms??item.start_utc_ms??item.at_utc_ms??item.created_at_utc_ms;if(time!=null)fields.push(["时间",when(time)]);for(const key of ["_event_group","key","value","kind","action","app_id","window_instance_id","title","url","quality","actor","actor_basis","result","data_status","status","summary"]){if(item[key]!=null&&item[key]!=="")fields.push([key,item[key]]);}return fields;}
function setHeaders(items){const keys=new Set();items.slice(0,30).forEach(i=>fieldsFor(i).forEach(([k])=>keys.add(k)));const preferred=["时间","_event_group","kind","action","app_id","title","summary","quality","ID"];const ordered=[...preferred.filter(k=>keys.has(k)),...keys].filter((k,i,a)=>a.indexOf(k)===i).slice(0,7);$("thead").innerHTML="<tr>"+ordered.map(k=>`<th>${html(k==="_event_group"?"来源":k)}</th>`).join("")+"</tr>";return ordered;}
function displayCell(value){if(value==null||value==="")return "-";if(typeof value==="object")return JSON.stringify(value);const text=String(value);return text.length>180?text.slice(0,177)+"…":text;}
function renderRows(items){const body=$("tbody");body.textContent="";if(!items.length){body.innerHTML='<tr><td class="empty" colspan="8">没有匹配数据</td></tr>';return;}const keys=setHeaders(items);items.forEach(item=>{const tr=document.createElement("tr");const id=idOf(item);if(id)tr.dataset.id=id;const map=new Map(fieldsFor(item));for(const key of keys){const td=document.createElement("td");let value=map.get(key);if(key==="时间")td.innerHTML=`<time>${html(value)}</time>`;else if(key==="kind"||key==="action")td.innerHTML=`<span class="kind">${html(displayCell(value))}</span>`;else td.textContent=displayCell(value);tr.append(td);}tr.onclick=()=>select(item);body.append(tr);});}
function statRows(data){const values=[];if(!data||typeof data!=="object")return"";for(const [k,v] of Object.entries(data)){if(["items","events","timeline","evidence","episodes","observations","data"].includes(k))continue;if(typeof v==="number"||typeof v==="string")values.push(`<div class="stat"><span>${html(k)}</span><b>${html(v)}</b></div>`);}return values.slice(0,9).join("");}
function refsFor(value){const refs=[],seen=new Set();function walk(node){if(!node||typeof node!=="object")return;for(const [key,values] of Object.entries(node)){if(["source_observation_ids","context_observation_ids","evidence_ids","asset_refs","item_ids","source_episode_ids","caused_by"].includes(key)&&Array.isArray(values)){for(const x of values){const id=typeof x==="object"?idOf(x):x,signature=key+":"+id;if(id&&!seen.has(signature)){seen.add(signature);refs.push({key,id});}}}else if(key==="episode_id"&&values){const signature=key+":"+values;if(!seen.has(signature)){seen.add(signature);refs.push({key,id:values});}}else if(typeof values==="object")walk(values);}}walk(value);return refs;}
function showRefs(value){$("refs").innerHTML="";for(const ref of refsFor(value)){const b=document.createElement("button");b.className="ref";b.textContent=ref.key+": "+ref.id;b.onclick=()=>jumpRef(ref);$("refs").append(b);}}
async function jumpRef(ref){if(ref.key==="asset_refs"){try{const record=await api("/api/assets/"+encodeURIComponent(ref.id));select({...record,asset_refs:[ref.id]});}catch(e){notice(e.message);}return;}const target=ref.key.includes("evidence")?"evidence":ref.key.includes("episode")?"episodes":ref.key.includes("item")?"items":"raw";tab=target;resetPage();updateControls();for(const b of document.querySelectorAll(".tabs button"))b.setAttribute("aria-selected",b.dataset.tab===tab);await load();select({id:ref.id});}
function clearAsset(){assetVersion++;if(assetUrl){URL.revokeObjectURL(assetUrl);assetUrl=null;}$("asset").style.display="none";$("asset").removeAttribute("src");}
async function loadAsset(value){clearAsset();const version=assetVersion;if(!$("reveal").checked)return;const refs=refsFor(value).filter(r=>r.key==="asset_refs");if(!refs.length)return;try{const r=await fetch("/api/assets/"+encodeURIComponent(refs[0].id)+"?download=1&include_content=1",{headers:{Authorization:"Bearer "+token}});if(!r.ok)return;const blob=await r.blob();if(version!==assetVersion||!$("reveal").checked)return;assetUrl=URL.createObjectURL(blob);$("asset").src=assetUrl;$("asset").style.display="block";}catch(e){if(version===assetVersion)notice(e.message);}}
function label(key){return ({observed_at_utc_ms:"观察时间",start_utc_ms:"开始时间",end_utc_ms:"结束时间",app_id:"应用",window_instance_id:"窗口",window_title:"窗口标题",browser_url:"浏览器 URL",ocr_text:"OCR 文本",uia_text:"UIA 文本",text_summary:"正文摘要",content_excerpt:"内容摘录",_event_group:"证据来源",source_observation_ids:"原始观察",evidence_ids:"证据",asset_refs:"资产"})[key]||key;}
function readable(value){if(!value||typeof value!=="object")return "";const lines=[];const basic=["_event_group","kind","action","app_id","window_instance_id","title","window_title","url","browser_url","actor","actor_basis","quality","status","result","data_status","summary"];const content=["text","text_summary","ocr_text","uia_text","content_excerpt","value"];for(const key of basic){if(value[key]!=null&&value[key]!=="")lines.push(label(key)+"\n"+String(value[key]));}for(const key of content){if(value[key]!=null&&value[key]!=="")lines.push("正文 / "+label(key)+"\n"+String(value[key]));}for(const key of ["source_observation_ids","context_observation_ids","evidence_ids","asset_refs","item_ids","source_episode_ids","caused_by"]){if(Array.isArray(value[key])&&value[key].length)lines.push(label(key)+"\n"+value[key].join("\n"));}for(const [key,nested] of Object.entries(value)){if(basic.includes(key)||content.includes(key)||["source_observation_ids","context_observation_ids","evidence_ids","asset_refs","item_ids","source_episode_ids","caused_by"].includes(key))continue;if(nested&&typeof nested==="object")lines.push(label(key)+"\n"+JSON.stringify(nested,null,2));else if(nested!=null&&nested!=="")lines.push(label(key)+"\n"+String(nested));}return lines.join("\n\n");}
function renderDetail(){if(selected)$("detail").textContent=jsonMode?JSON.stringify(selected,null,2):readable(selected);$("pretty").setAttribute("aria-selected",!jsonMode);$("json").setAttribute("aria-selected",jsonMode);}
async function select(value){selected=value;jsonMode=false;$("detail-label").textContent=idOf(value)||"选中记录";showRefs(value);renderDetail();loadAsset(value);const enriched=await detailFor(value);if(selected===value&&enriched&&enriched!==value){selected=enriched;$("detail-label").textContent=idOf(enriched)||"选中记录";showRefs(enriched);renderDetail();loadAsset(enriched);}}
function notice(message){const el=$("notice");el.textContent=message||"";el.style.display=message?"block":"none";}
function endpointFor(value){return ({raw:"/api/browser/observations",items:"/api/knowledge/items",episodes:"/api/knowledge/episodes",evidence:"/api/knowledge/evidence",events:"/api/knowledge/events",timeline:"/api/knowledge/timeline",context:"/api/knowledge/context",summary:"/api/knowledge/summary",candidates:"/api/knowledge/memory/candidates",facts:"/api/knowledge/memory/facts",diagnostics:"/api/status"})[value]||"/api/knowledge/context";}
function detailEndpoint(value,id){if(value==="raw")return "/api/events/"+encodeURIComponent(id);if(value==="items")return "/api/knowledge/items/"+encodeURIComponent(id);if(value==="episodes")return "/api/knowledge/episodes/"+encodeURIComponent(id);return null;}
async function detailFor(item){const id=idOf(item);if(!id)return item;if(tab==="evidence"){try{const result=await api("/api/knowledge/evidence?id="+encodeURIComponent(id)+"&include_assets="+($("reveal").checked?1:0));return result.evidence?.[0]||item;}catch{return item;}}const endpoint=detailEndpoint(tab,id);if(!endpoint)return item;try{return await api(endpoint+"?include_content="+($("reveal").checked?1:0));}catch{return item;}}
async function load(preserveId=""){
  const version=++loadVersion, requestedTab=tab; loading=true; notice(""); $("list-title").textContent=titleFor(tab); $("data-status").textContent="读取中…";
  try {
    const p=query(); let data;
    if(tab==="diagnostics"){
      const [status,metrics,health]=await Promise.all([api("/api/status"),api("/api/metrics"),api("/api/knowledge/health")]); data={...status,metrics,knowledge:health};
    } else {
      const path=endpointFor(tab); if(tab==="raw"){p.delete("after_item");if(cursor)p.set("before",cursor);} else if(cursor)p.set(tab==="episodes"?"after_episode":"after_item",cursor); data=await api(path+"?"+p);
    }
    if(version!==loadVersion||requestedTab!==tab)return;
    const items=listOf(data); $("summary").innerHTML=statRows(data); $("count").textContent=`${items.length} 条`; $("data-status").textContent=data.data_status||data.status||"ok"; renderRows(items);
    nextCursor=data.next_cursor||data.nextCursor||data.cursor_next||(tab==="raw"&&data.has_more?data.cursor:null); const has=Boolean((data.truncated||data.has_more)&&nextCursor); $("next").disabled=!has; $("prev").disabled=page<=1; $("page-label").textContent=`第 ${page} 页`; const retained=preserveId?items.find(item=>idOf(item)===preserveId):null; if(retained)select(retained); else if(items.length&&!selected)select(items[0]);
  } catch(e) { if(version===loadVersion&&requestedTab===tab){notice(e.message); $("data-status").textContent="error"; renderRows([]);} } finally { if(version===loadVersion)loading=false; }
}
async function refreshStatus(){try{const data=await api("/api/status");$("summary").innerHTML=statRows(data);$("data-status").textContent=data.status||"ok";}catch(e){notice(e.message);}}
function clearDetail(){selected=null;clearAsset();$("detail").textContent="";$("refs").textContent="";$("detail-label").textContent="";}
function resetPage(){loadVersion++;page=1;cursor="";cursors=[""];nextCursor="";clearDetail();}
function updateControls(){const memory=tab==="candidates"||tab==="facts";for(const id of ["q","start","end","app","kind"])$(id).disabled=memory||tab==="diagnostics";$("mode-switch").style.display=tab==="context"?"flex":"none";}
document.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>{tab=b.dataset.tab;resetPage();updateControls();document.querySelectorAll(".tabs button").forEach(x=>x.setAttribute("aria-selected",x===b));load();});
$("apply").onclick=()=>{resetPage();load();};$("refresh").onclick=()=>{const keep=idOf(selected);clearDetail();load(keep);};$("prev").onclick=()=>{if(page>1){page--;cursor=cursors[page-1]||"";clearDetail();load();}};$("next").onclick=()=>{if(!nextCursor)return;cursor=nextCursor;cursors[page]=cursor;page++;clearDetail();load();};$("reveal").onchange=()=>{resetPage();load();};
$("pretty").onclick=()=>{jsonMode=false;renderDetail();};$("json").onclick=()=>{jsonMode=true;renderDetail();};
document.querySelectorAll("[data-mode]").forEach(button=>button.onclick=()=>{contextMode=button.dataset.mode;document.querySelectorAll("[data-mode]").forEach(item=>item.setAttribute("aria-pressed",item===button));resetPage();load();});
$("download").onclick=()=>{if(!selected)return;const blob=new Blob([JSON.stringify(selected,null,2)],{type:"application/json"});const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download=(idOf(selected)||"activity")+".json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$("endpoint").textContent=location.host+" · 只读浏览";updateControls();load();
</script>
</body></html>'''


__all__ = ["BROWSER_HTML"]
