/** Actual original client -> owned Host -> SQLite -> native Session, no GUI/provider. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {readFileSync} from 'node:fs';
import {mkdtemp, readFile, rm, realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, dirname, basename} from 'node:path';
import {fileURLToPath} from 'node:url';
import {stripTypeScriptTypes} from 'node:module';
import {setTimeout as delay} from 'node:timers/promises';

const root = fileURLToPath(new URL('../', import.meta.url));
const moduleUrl = source => 'data:text/javascript;base64,' + Buffer.from(stripTypeScriptTypes(source, {mode:'transform'})).toString('base64');
const reads = moduleUrl(readFileSync(join(root,'frontend/control-ui/ui/src/app/mirally-read-models.ts'),'utf8'));
const reminders = moduleUrl(readFileSync(join(root, 'frontend/control-ui/ui/src/app/desktop-reminders.ts'), 'utf8'));
const {MirallyBrowserClient} = await import(moduleUrl(readFileSync(join(root,'frontend/control-ui/ui/src/app/mirally-client.ts'),'utf8')
  .replace(/(["'])\.\/mirally-read-models\.(?:ts|js)\1/g, () => JSON.stringify(reads))
  .replace(/(["'])\.\/desktop-reminders\.(?:ts|js)\1/g, () => JSON.stringify(reminders))));
let requestsSource = readFileSync(join(root,'frontend/control-ui/ui/src/lib/sessions/session-requests.ts'),'utf8');
for (const name of ['session-archive-timeout','session-list-limits']) {
  requestsSource=requestsSource.replaceAll(`../../../../src/shared/${name}.ts`,moduleUrl(readFileSync(join(root,`frontend/control-ui/src/shared/${name}.ts`),'utf8')));
}
const {requestSessionPatch}=await import(moduleUrl(requestsSource));
// Exercise the retained organizer, including its toast lifetime and captured
// receipt, against the real client. Only unrelated UI dependencies are stubs.
const organizerSource = stripTypeScriptTypes(readFileSync(join(root,'frontend/control-ui/ui/src/components/session-organizer-operations.runtime.ts'),'utf8'),{mode:'transform'})
  .replace(/^import[\s\S]*?from\s+["'][^"']+["'];\s*/gm,'')
  .replace(/^export\s*\{[\s\S]*?\}\s*from\s+["'][^"']+["'];\s*/gm,'');
const organizer = await import(moduleUrl(`export const harness = {toast:null,dialog:null};
  const registerNewSessionSetupEnglish = () => {};
  const t = value => value;
  const showToast = value => {harness.toast=value;};
  const showInputDialog = async value => {harness.dialog=value;return null;};
  const resolveSessionRenamePatch = label => ({label});
  const formatUiError = error => error?.message ?? String(error);
  const sessionRowAgentId = () => 'main';
  const requireSessionMutationAccess = () => true;
  const withSessionWorkspaceRecovery = ({request}) => request();
  ${organizerSource}`));
async function until(check, label, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {const value = await check(); if (value) return value; await delay(10);}
  throw new Error('Timed out: ' + label);
}

test('conversation title, pin and archive are real owned metadata with version fencing and restart recovery', {timeout:60000}, async t => {
  const home = await mkdtemp(join(tmpdir(),'mirally organization 会话 '));
  let child, runtime, logs = '';
  const clients = [], calls = [], previous = new Map(['window','document','location','fetch'].map(name => [name,Object.getOwnPropertyDescriptor(globalThis,name)]));
  const nativeFetch = globalThis.fetch;
  t.after(async () => {
    for (const client of clients) client.stop();
    if (child?.exitCode === null) {child.stdin.end(); await Promise.race([once(child,'exit'),delay(8000,undefined,{ref:false})]); if (child.exitCode === null) {child.kill(); await once(child,'exit');}}
    for (const [name,value] of previous) {if(value)Object.defineProperty(globalThis,name,value);else delete globalThis[name];}
    const canonical = await realpath(home), temporary = await realpath(tmpdir());
    assert.equal(dirname(canonical),temporary); assert.ok(basename(canonical).startsWith('mirally organization '));
    await rm(canonical,{recursive:true,force:true});
  });
  async function start() {
    const python = join(root,'.venv',process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
    child = spawn(process.execPath,[join(root,'kernel/dsh/packages/mirally/mirally-product/start.mjs'),'--root',root,'--home',home,'--python',python,'--test-mode'],
      {cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'},stdio:['pipe','pipe','pipe']});
    child.stdout.on('data',bytes=>{logs+=bytes;});child.stderr.on('data',bytes=>{logs+=bytes;});
    try {
      runtime = await until(async()=>{try{return JSON.parse(await readFile(join(home,'runtime.json'),'utf8'));}catch{return false;}},'Host startup');
    } catch (error) {
      throw new Error(`${error.message}; child exit=${child.exitCode}\n${logs}`,{cause:error});
    }
    Object.assign(globalThis,{window:{dispatchEvent(){}},document:{getElementById:()=>({})},location:{origin:runtime.url,hash:''},
      fetch:(input,options)=>{calls.push({input:String(input),options});return nativeFetch(new URL(input,runtime.url),options);}});
  }
  const client = () => {const value=new MirallyBrowserClient({token:runtime.token});clients.push(value);return value;};
  const http = async (path, body, method='POST') => {
    const response=await nativeFetch(runtime.url+path,{method,headers:{authorization:'Bearer '+runtime.token,'content-type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});
    return {status:response.status,body:await response.json()};
  };
  await start();
  const first=client(), stale=client();
  const session=await first.request('sessions.create',{displayName:'原来的生活计划',idempotencyKey:'metadata-session'});
  const key=session.key, id=key.replace(/^agent:main:/,'');
  const original=(await first.request('sessions.list',{archived:'all'})).sessions.find(row=>row.key===key);
  await stale.request('sessions.list',{archived:'all'});
  assert.equal(original.metadataVersion,1);
  const renamed=await first.request('sessions.patch',{key,label:'改名后的计划',expectedMetadataVersion:1,expectedSessionId:original.sessionId});
  assert.equal(renamed.entry.metadataVersion,2);assert.equal(renamed.entry.label,'改名后的计划');
  assert.equal(renamed.entry.sessionId,original.sessionId);
  await assert.rejects(stale.request('sessions.patch',{key,pinned:true,expectedMetadataVersion:1}),error=>error.code==='CONFLICT');
  const pin=await first.request('sessions.patch',{key,pinned:true,expectedMetadataVersion:2});
  assert.equal(pin.entry.pinned,true);assert.equal(typeof pin.entry.pinnedAt,'number');
  const before=calls.length;
  for(const patch of [{permissionMode:'full'},{thinkingLevel:'max'},{unread:true},{incognito:true},{fork:true},{category:'group'}]) {
    assert.equal(first.supportsSessionPatch({key,...patch}),false);
    await assert.rejects(first.request('sessions.patch',{key,...patch}),/不支持/);
  }
  assert.equal(first.supportsSessionPatch({key,model:null}),true);
  await assert.rejects(first.request('sessions.patch',{key,model:'other'}),/请选择已配置/);
  assert.equal(calls.length,before,'Unsupported metadata fields must fail before HTTP effects');
  const archived=await first.request('sessions.patch',{key,archived:true,expectedMetadataVersion:3});
  assert.equal(archived.entry.archived,true);
  assert.equal((await first.request('sessions.list')).sessions.some(row=>row.key===key),false);
  assert.equal((await first.request('sessions.list',{archived:true})).sessions[0].key,key);
  assert.equal((await first.request('sessions.list',{archived:'all'})).sessions[0].key,key);
  await assert.rejects(first.request('chat.send',{sessionKey:key,message:'误发',idempotencyKey:'must-not-send'}),/恢复/);
  assert.equal((await http(`/api/sessions/${id}/messages`,{commandId:'direct-must-not-send',text:'误发'})).status,409);
  assert.equal((await http('/api/materials',{conversation_id:id,name:'误加.txt',media_type:'text/plain',data:Buffer.from('must not upload').toString('base64')})).status,409);
  assert.equal((await first.request('chat.history',{sessionKey:key})).messages.length,0);
  assert.equal((await http('/api/sessions?archived=active',undefined,'GET')).body.sessions.length,0);
  assert.equal((await http('/api/sessions?archived=archived',undefined,'GET')).body.sessions[0].id,id);
  for(const value of clients)value.stop();
  child.stdin.end();await once(child,'exit');
  await start();
  const fresh=client(), restored=(await fresh.request('sessions.list',{archived:'all'})).sessions[0];
  assert.equal(restored.label,'改名后的计划');assert.equal(restored.pinned,true);assert.equal(restored.archived,true);
  assert.equal(restored.metadataVersion,4);assert.equal(restored.sessionId,original.sessionId);
  const active=await fresh.request('sessions.patch',{key,archived:false,expectedMetadataVersion:4});
  assert.equal(active.entry.archived,false);assert.equal(active.entry.metadataVersion,5);
  assert.equal((await fresh.request('sessions.list')).sessions[0].key,key);
  const unsupported=await http(`/api/sessions/${id}`,{expected_version:5,patch:{permissionMode:'full'}},'PATCH');
  assert.equal(unsupported.status,400);
  const reopened=(await http(`/api/sessions/${id}/history`,undefined,'GET')).body;
  assert.equal(reopened.messages.length,0);
  assert.equal((await http('/api/rpc',{method:'command.list',params:{conversation_id:id}})).body.result.commands.length,0);

  const mutationErrors=[], patchVersions=[];
  const scope={selectedAgentId:'main',sessions:{state:{},
    patch:async (key,patch,options)=>{patchVersions.push(options.expectedMetadataVersion);return requestSessionPatch(fresh,key,patch,options);},
    beginArchive:()=>()=>{},captureConnectionScope:()=>({}),isConnectionScopeCurrent:()=>true,
    invalidate(){},refreshReplacement:()=>fresh.request('sessions.list')}};
  const host={pruneSidebarSessionEntry(){},selectSession(){},sidebarSessionStatusFilter:()=> 'active',sessionData:{
    isSessionMutationScopeCurrent:()=>true,publishSessionMutationError:(_scope,error)=>mutationErrors.push(error),refreshSidebarSessions:async()=>{}}};
  const row=Object.freeze({key,sessionId:original.sessionId,metadataVersion:5,agentId:'main',pinned:true,archived:false,label:'改名后的计划'});
  await organizer.archiveSessionWithUndo(host,row,scope);
  assert.equal(organizer.harness.toast.actionLabel,'common.undo');
  organizer.harness.toast.onAction();
  await until(()=>patchVersions.length===2,'Undo dispatch');
  await until(async()=>!(await http('/api/sessions?archived=all',undefined,'GET')).body.sessions[0].archived,'Undo receipt');
  assert.deepEqual(patchVersions,[5,6],'Undo must use its own archive receipt revision');
  assert.equal(row.metadataVersion,5,'Captured row must remain immutable');
  assert.deepEqual(mutationErrors,[]);

  await organizer.archiveSessionWithUndo(host,{...row,metadataVersion:7},scope);
  await fresh.request('sessions.patch',{key,label:'归档后另一处编辑',expectedMetadataVersion:8});
  organizer.harness.toast.onAction();
  await until(()=>mutationErrors.length,'Conflicting Undo rejection');
  assert.equal(mutationErrors[0].code,'CONFLICT','Undo must reject an intervening change');
  assert.equal((await http('/api/sessions?archived=all',undefined,'GET')).body.sessions[0].archived,true);
  await organizer.renameSession(host,{...row,metadataVersion:8,renameValue:'归档后另一处编辑'},scope);
  const refused=await organizer.harness.dialog.submit('需要保留的草稿');
  assert.ok(typeof refused==='string' && refused.length>0,'A stale rename must return an error to the still-open input dialog');
  assert.equal((await http('/api/sessions?archived=all',undefined,'GET')).body.sessions[0].title,'归档后另一处编辑');
});
