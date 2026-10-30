import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';

const require=createRequire(new URL('../frontend/control-ui/package.json',import.meta.url));
const ts=require('typescript');
const source=readFileSync(new URL('../frontend/control-ui/ui/src/pages/mirally/voice-input.ts',import.meta.url),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022,experimentalDecorators:true,useDefineForClassFields:false}}).outputText.replace(/^import[\s\S]*?;\s*/gm,'');
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};

function fixture({media,request}) {
  let Component;let stopped=0;const calls=[];const stream={getTracks:()=>[{stop:()=>stopped++}]};
  class Recorder {
    constructor(){this.state='inactive';this.mimeType='audio/webm';}
    start(){this.state='recording';}
    stop(){this.state='inactive';this.ondataavailable?.({data:new Blob(['voice'],{type:'audio/webm'})});this.onstop?.();}
  }
  class Reader {readAsDataURL(){this.result='data:audio/webm;base64,dm9pY2U=';queueMicrotask(()=>this.onload?.());}}
  const sandbox={html:()=>'',nothing:Symbol(),property:()=>()=>{},state:()=>()=>{},icons:{mic:''},
    OpenClawLightDomElement:class{isConnected=true;disconnectedCallback(){}},
    customElements:{define:(_,component)=>{Component=component;}},navigator:{mediaDevices:{getUserMedia:()=>media?media.promise:Promise.resolve(stream)}},
    MediaRecorder:Recorder,FileReader:Reader,Blob,AbortController,crypto:{randomUUID:()=> 'voice-request-123456789'},
    setTimeout:()=>1,clearTimeout(){},queueMicrotask,requestProduct:async(path,options)=>{calls.push(JSON.parse(options.body));return request?.(path,options)??{};}};
  vm.createContext(sandbox);vm.runInContext(compiled,sandbox);const component=new Component();component.sessionKey='first';
  return {component,stream,calls,get stopped(){return stopped;}};
}

test('switching while microphone permission is pending releases the late stream',async()=>{
  const media=deferred(), f=fixture({media});const started=f.component.start();f.component.sessionKey='second';
  f.component.updated(new Map([['sessionKey','first']]));media.resolve(f.stream);await started;
  assert.equal(f.stopped,1);assert.equal(f.component.phase,'idle');assert.deepEqual(f.calls,[]);
});

test('microphone permission denial leaves an actionable error and no active capture',async()=>{
  const f=fixture({media:{promise:Promise.reject({name:'NotAllowedError'})}});
  await f.component.start();assert.equal(f.component.phase,'idle');assert.match(f.component.error,/麦克风权限/);
  assert.deepEqual(f.calls,[]);
});

test('cancelled recording detaches callbacks before the next recording begins',async()=>{
  const f=fixture({});await f.component.start();const old=f.component.recorder;
  f.component.cancel();assert.equal(old.ondataavailable,null);assert.equal(old.onstop,null);assert.equal(old.onerror,null);
  await f.component.start();assert.equal(f.component.chunks.length,0);assert.equal(f.component.phase,'recording');f.component.cancel();
});

test('switching during recognition cancels and never puts a late result in the new conversation',async()=>{
  const pending=deferred(),changes=[];
  const f=fixture({request:(_path,options)=>JSON.parse(options.body).method==='speech.transcribe'?pending.promise:{}});
  f.component.getDraft=()=> '旧草稿';f.component.onDraftChange=value=>changes.push(value);
  await f.component.start();f.component.stop();await settle();
  assert.equal(f.component.phase,'transcribing');f.component.sessionKey='second';f.component.updated(new Map([['sessionKey','first']]));
  pending.resolve({result:{text:'迟到识别'}});await settle();
  assert.equal(f.component.phase,'idle');assert.equal(f.stopped,1);assert.deepEqual(changes,[]);
  assert.equal(f.calls.filter(call=>call.method==='speech.cancel').length,1);
});

test('successful recognition appends to the latest draft rather than overwriting subsequent typing',async()=>{
  const pending=deferred(),changes=[];let draft='开始';
  const f=fixture({request:(_path,options)=>JSON.parse(options.body).method==='speech.transcribe'?pending.promise:{}});
  f.component.getDraft=()=>draft;f.component.onDraftChange=value=>changes.push(value);
  await f.component.start();f.component.stop();await settle();draft='正在编辑';pending.resolve({result:{text:'识别文字'}});await settle();
  assert.deepEqual(changes,['正在编辑 识别文字']);assert.equal(f.component.phase,'idle');
});
