/** Capability drafts and save failures exercise the real controller without a Host. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
const ts=createRequire(new URL('../frontend/control-ui/package.json',import.meta.url))('typescript');
const html=(strings,...values)=>({strings,values});
const stringify=value=>value==null||typeof value==='symbol'||typeof value==='function'?'':Array.isArray(value)?value.map(stringify).join(''):value?.strings?value.strings.map((part,index)=>part+stringify(value.values[index])).join(''):String(value);
const source=ts.transpileModule(readFileSync(new URL('../frontend/control-ui/ui/src/pages/mirally/capabilities-settings.ts',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022,experimentalDecorators:true,useDefineForClassFields:false}}).outputText.replace(/^import[\s\S]*?;\s*/gm,'').replace(/^export /gm,'');
const config=()=>({revision:7,read_roots:['C:/资料'],web_enabled:false,workspace_write_enabled:false,search_connection:'chat',workspace:'C:/workspace'});
function fixture(handler) {
  const calls=[];
  const sandbox={html,nothing:Symbol(),live:value=>value,customElement:()=>target=>target,state:()=>()=>{},
    OpenClawLightDomElement:class{isConnected=true;connectedCallback(){}disconnectedCallback(){}},
    renderSettingsSection:(_options,content)=>content,renderSettingsRow:options=>html`${options.title}${options.description}${options.control}`,
    errorMessage:error=>error.message,rpcProduct:(method,params)=>{calls.push({method,params});return handler(method,params);}};
  vm.runInNewContext(source+'\nglobalThis.Page=MirallyCapabilitiesSettings;',sandbox);
  return {page:new sandbox.Page(),calls};
}
test('failed initial capability load shows retry instead of editable unconfirmed permissions',async()=>{
  const f=fixture(async()=>{throw new Error('offline');});
  await f.page.reload();
  const rendered=stringify(f.page.render());
  assert.match(rendered,/offline/);assert.match(rendered,/重新读取/);
  assert.doesNotMatch(rendered,/mirally-capabilities-(?:roots|write|web|save)/);
  assert.equal(f.page.busy,false);
});
test('capability choices stay draft until save and retain expected revision and scoped patch',async()=>{
  let saved;
  const f=fixture(async(method,params)=>{
    if(method==='capabilities.get')return {...config(),...(saved?.patch??{}),revision:saved?8:7};
    if(method==='model.connections.list')return {connections:[]};
    if(method==='capabilities.configure'){saved=params;return {};}
    throw new Error(method);
  });
  await f.page.reload();
  f.page.web=true;f.page.write=true;f.page.paths=' C:/资料 \n\n D:/文档 ';
  assert.equal(f.calls.filter(call=>call.method==='capabilities.configure').length,0);
  const rendered=stringify(f.page.render());
  assert.match(rendered,/type="checkbox"/);assert.doesNotMatch(rendered,/role="switch"/);
  assert.match(rendered,/aria-label="授权只读文件夹"/);assert.match(rendered,/aria-label="网页服务连接"/);
  await f.page.save();
  assert.deepEqual(JSON.parse(JSON.stringify(saved)),{expected_revision:7,patch:{read_roots:['C:/资料','D:/文档'],search_connection:'chat',web_enabled:true,workspace_write_enabled:true}});
  assert.equal(f.page.config.revision,8);assert.equal(f.page.notice,'能力范围已保存。');
});
test('detaching during configure prevents late reload or success feedback',async()=>{
  let complete;
  const f=fixture(method=>method==='capabilities.configure'?new Promise(resolve=>{complete=resolve;}):Promise.resolve(method==='capabilities.get'?config():{connections:[]}));
  await f.page.reload();
  const saving=f.page.save();
  f.page.disconnectedCallback();f.page.isConnected=false;
  complete({});await saving;
  assert.equal(f.calls.length,3);assert.equal(f.page.notice,'');
});
test('failed post-save reload does not claim a confirmed refreshed scope',async()=>{
  let configured=false;
  const f=fixture(async(method)=>{
    if(method==='capabilities.configure'){configured=true;return {};}
    if(configured)throw new Error('reload failed');
    return method==='capabilities.get'?config():{connections:[]};
  });
  await f.page.reload();await f.page.save();
  assert.equal(f.page.notice,'');assert.equal(f.page.error,'reload failed');assert.equal(f.page.busy,false);
});
