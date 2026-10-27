import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {existsSync, readFileSync} from 'node:fs';
import {mkdtemp,readFile,rm,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,basename,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
const root=fileURLToPath(new URL('../',import.meta.url));
async function until(f,label,ms=20000){let end=Date.now()+ms;while(Date.now()<end){const x=await f();if(x)return x;await delay(25);}throw Error('Timed out: '+label);}

test('Host persists per-conversation model and dispatches real HTTP including queued selections', {timeout:90000}, async t=>{
  const python=join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
  if(!existsSync(python)||!existsSync(join(root,'kernel/dsh/packages/core/agent/lib/index.js'))){t.skip('Prepare repository Python and built kernel');return;}
  let release; const held=new Promise(r=>release=r), requests=[];
  const server=createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    const body=JSON.parse(Buffer.concat(chunks));requests.push(body);
    if(requests.length===1)await held;
    res.writeHead(200,{'content-type':'text/event-stream'});
    for(const frame of [{id:'test',choices:[{index:0,delta:{role:'assistant',content:'Local model response'},finish_reason:null}]},{id:'test',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:1,completion_tokens:1,total_tokens:2}}])res.write(`data: ${JSON.stringify(frame)}\n\n`);
    res.end('data: [DONE]\n\n');
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const home=await mkdtemp(join(tmpdir(),'mirally model host '));let child,runtime,logs='';
  t.after(async()=>{release();if(child?.exitCode===null){child.stdin.end();try{await Promise.race([once(child,'exit'),delay(5000).then(()=>{throw Error('cleanup');})]);}catch{child.kill();await Promise.race([once(child,'exit'),delay(3000)]);}}server.closeAllConnections();await new Promise(r=>server.close(r));const path=await realpath(home);assert.equal(dirname(path),await realpath(tmpdir()));assert.ok(basename(path).startsWith('mirally model host '));await rm(path,{recursive:true,force:true});});
  async function start(){child=spawn(process.execPath,[join(root,'kernel/dsh/packages/mirally/mirally-product/start.mjs'),'--root',root,'--home',home,'--python',python],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'},stdio:['pipe','pipe','pipe']});child.stdout.on('data',b=>logs+=b);child.stderr.on('data',b=>logs+=b);runtime=await until(async()=>{try{const v=JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));return v.pid===child.pid&&v;}catch{return false;}},'host start');}
  async function api(path,method='GET',body,expected=200){const r=await fetch(new URL(path,runtime.url),{method,headers:{authorization:'Bearer '+runtime.token,'content-type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});assert.equal(r.status,expected,await r.clone().text());return r.json();}
  const rpc=async(method,params,expected=200)=>api('/api/rpc','POST',{method,params},expected);
  const send=async(id,commandId,text,materials=[])=>api(`/api/sessions/${id}/messages`,'POST',{commandId,text,materials},202);
  const select=async(id,model)=>{const rows=await api('/api/sessions');const session=rows.sessions.find(s=>s.id===id);return api(`/api/sessions/${id}`,'PATCH',{expected_version:session.metadata_version,patch:{model_selection:{connection_id:'default',model_id:model}}});};
  await start();
  await rpc('settings.update',{patch:{model:{provider:'openai-compatible',api_format:'openai-chat',base_url:`http://127.0.0.1:${server.address().port}/v1`,api_key:'local-secret',model_id:'model-a',image_enabled:false}}});
  await rpc('model.catalog.save',{connection_id:'default',model:{model_id:'model-b',image_enabled:true}});
  const one=await api('/api/sessions','POST',{title:'one',requestId:'create-one',model_selection:{connection_id:'default',model_id:'model-a'}},201),
    two=await api('/api/sessions','POST',{title:'two',model_selection:{connection_id:'default',model_id:'model-b'}},201);
  assert.equal(one.model_selection.model_id,'model-a');assert.equal(two.model_selection.model_id,'model-b');
  await send(one.id,'run-1','hold-first');await until(()=>requests.length===1,'first request');assert.equal(requests[0].model,'model-a');
  await rpc('model.catalog.save',{connection_id:'default',model:{model_id:'model-a',max_tokens:2000}},400);
  await rpc('model.connection.save',{connection_id:'default',model:{temperature:0.1}},400);
  await select(one.id,'model-b');
  const retry=await api('/api/sessions','POST',{title:'one',requestId:'create-one',model_selection:{connection_id:'default',model_id:'model-a'}},201);
  assert.equal(retry.id,one.id);assert.equal(retry.model_selection.model_id,'model-b');
  await send(one.id,'run-2','queued-second');
  await rpc('model.catalog.save',{connection_id:'default',model:{model_id:'model-b',max_tokens:2000}},400);
  await send(two.id,'run-3','independent-session');try{await until(()=>requests.some(r=>r.model==='model-b'),'second conversation request',7000);}catch(e){throw Error(e.message+'\nrequests='+JSON.stringify(requests.map(r=>({model:r.model,messages:r.messages.filter(m=>m.role==='user').map(m=>m.content)})))+'\n'+logs);}
  release();await until(()=>requests.length>=3,'queued request');
  assert.equal(requests[2].model,'model-b','queued command uses model selected when admitted');
  await until(async()=>{const x=await api('/api/sessions');return x.sessions.find(s=>s.id===one.id)?.model_selection?.model_id==='model-b';},'selection saved');
  await select(one.id,'model-a');
  await api('/api/shutdown','POST',{});await until(()=>child.exitCode!==null,'host exit');await start();
  const rows=await api('/api/sessions');assert.equal(rows.sessions.find(s=>s.id===one.id).model_selection.model_id,'model-a');assert.equal(rows.sessions.find(s=>s.id===two.id).model_selection.model_id,'model-b');
  const png=readFileSync(join(root,'mirally/assets/mirally-128.png')).toString('base64');
  const material=await api('/api/materials','POST',{conversation_id:two.id,request_id:'image-1',name:'image.png',media_type:'image/png',data:png},201);
  await send(two.id,'run-4','image-turn',[material.id]);await until(()=>requests.length>=4,'image model request');assert.equal(requests.at(-1).model,'model-b');assert.ok(requests.at(-1).messages.some(m=>Array.isArray(m.content)&&m.content.some(p=>p.type==='image_url')),'image reaches selected provider');
  await select(two.id,'model-a');const before=requests.length;
  await api(`/api/sessions/${two.id}/messages`,'POST',{commandId:'run-5',text:'text model image',materials:[material.id]},400);await delay(200);assert.equal(requests.length,before,'text-only model must reject image without HTTP');
  await api('/api/shutdown','POST',{});await until(()=>child.exitCode!==null,'host exit');
});
