/** Invalid hidden model controls must be revealed before native validation focuses them. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
const ts=createRequire(new URL('../frontend/control-ui/package.json',import.meta.url))('typescript');
const source=ts.transpileModule(readFileSync(new URL('../frontend/control-ui/ui/src/pages/mirally/settings-page.ts',import.meta.url),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022,experimentalDecorators:true,useDefineForClassFields:false}}).outputText.replace(/^import[\s\S]*?;\s*/gm,'').replace(/^export /gm,'');
function fixture() {
  class Element {constructor(details){this.details=details;}closest(selector){assert.equal(selector,'details');return this.details;}}
  const sandbox={html:(strings,...values)=>({strings,values}),nothing:Symbol(),live:value=>value,
    customElement:()=>target=>target,state:()=>()=>{},property:()=>()=>{},consume:()=>()=>{},applicationContext:{},
    eventOptions:options=>(_target,_name,descriptor)=>Object.assign(descriptor.value,options),
    HTMLElement:Element,OpenClawLightDomElement:class{},SubscriptionsController:class{watch(){return this;}},
    renderSettingsSection:(_options,content)=>content,renderSettingsRow:options=>options.control,
    renderSettingsEmpty:()=>'',isDeepSeekEndpoint:()=>false};
  vm.runInNewContext(source+'\nglobalThis.Page=MirallySettingsPage;',sandbox);
  return {page:new sandbox.Page(),Element};
}
test('model form captures non-bubbling invalid events and opens the invalid field disclosure',()=>{
  const {page,Element}=fixture();
  const template=page.modelPage();
  const index=template.strings.findIndex(part=>part.includes('@invalid='));
  assert.ok(index>=0);
  const listener=template.values[index];
  assert.equal(listener.capture,true,'native invalid does not bubble');
  const details={open:false};let prevented=false;
  listener.call(page,{target:new Element(details),preventDefault(){prevented=true;}});
  assert.equal(details.open,true);assert.equal(prevented,false,'native validation retains its feedback and focus');
});
test('invalid basic controls and non-element targets do not throw or cancel native validation',()=>{
  const {page,Element}=fixture();
  assert.doesNotThrow(()=>page.revealInvalidModelField({target:new Element(null)}));
  assert.doesNotThrow(()=>page.revealInvalidModelField({target:null}));
});
function templates(value,result=[]) {
  if(Array.isArray(value))value.forEach(item=>templates(item,result));
  else if(value?.strings){result.push(value);value.values.forEach(item=>templates(item,result));}
  return result;
}
test('initial model reasoning selects the actual off draft instead of the first default option',()=>{
  const {page}=fixture();page.model={reasoning_effort:'off'};
  const options=templates(page.modelPage()).filter(item=>item.strings[0]==='<option value='&&item.strings[1]?.includes('.selected='));
  assert.equal(options.find(item=>item.values[0]==='off').values[1],true);
  assert.equal(options.find(item=>item.values[0]==='default'&&item.values[2]==='服务默认').values[1],false);
});
test('named connection and routing option selection follows the saved identity on first render',()=>{
  const {page}=fixture();
  page.settings={model:{},model_connections:{other:{name:'Other',model:{}}},model_routes:{chat:'other',background:'default'}};
  page.connectionId='other';
  const options=templates(page.connectionSection()).filter(item=>item.strings[0]==='<option value=');
  assert.equal(options.find(item=>item.values[0]==='other').values[1],true);
  assert.equal(options.find(item=>item.values[0]==='default').values[1],false);
  const routes=templates(page.routingSection()).filter(item=>item.strings[0]==='<option value='&&item.values[0]==='other');
  assert.deepEqual(routes.map(item=>item.values[1]),[true,false]);
});
