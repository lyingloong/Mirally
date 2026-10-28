/** Exercise product chat controls without a browser or a live product home. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';

const requireUi=createRequire(new URL('../frontend/control-ui/package.json',import.meta.url));
const ts=requireUi('typescript');
const html=(strings,...values)=>({strings,values});
const stringify=value=>value==null||typeof value==='symbol'||typeof value==='function'?'':Array.isArray(value)?value.map(stringify).join(''):value?.strings?value.strings.map((s,i)=>s+stringify(value.values[i])).join(''):String(value);
const plain=value=>JSON.parse(JSON.stringify(value));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const permission=(session_auto_approve=false,default_trusted=false)=>({session_auto_approve,default_trusted});
const plan=(active=false,pending)=>({available:true,active,...(pending===undefined?{}:{pending})});
const compiled=new Map();

function fixture(handler,{file='chat-options',className='MirallyChatOptions'}={}) {
  if(!compiled.has(file)) {
    const source=readFileSync(new URL(`../frontend/control-ui/ui/src/pages/mirally/${file}.ts`,import.meta.url),'utf8');
    compiled.set(file,ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022,experimentalDecorators:true,useDefineForClassFields:false}}).outputText.replace(/^import[\s\S]*?;\s*/gm,'').replace(/^export /gm,''));
  }
  const calls=[],listeners=new Map(),navigation=[];
  const sandbox={html,nothing:Symbol('nothing'),customElement:()=>target=>target,property:()=>()=>{},state:()=>()=>{},
    ref:()=>'',syncDropdownItemRadio:()=>{},icons:{check:'check-icon',chevronDown:'chevron-icon'},
    OpenClawLightDomElement:class{isConnected=true;connectedCallback(){} disconnectedCallback(){}},
    window:{addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)},
    setTimeout:()=>1,clearTimeout:()=>{},errorMessage:error=>error.message,
    openProductPage:async page=>navigation.push(page),
    rpcProduct:(method,params)=>{calls.push({method,params});return handler(method,params);}};
  vm.runInNewContext(compiled.get(file)+`\nglobalThis.Page=${className};`,sandbox);
  return {page:new sandbox.Page(),calls,listeners,navigation};
}

function templates(value,result=[]) {
  if(Array.isArray(value)) value.forEach(item=>templates(item,result));
  else if(value?.strings) {result.push(value);value.values.forEach(item=>templates(item,result));}
  return result;
}
function choices(page) {
  return templates(page.render()).filter(item=>item.strings[0].startsWith('<wa-dropdown-item value=')&&item.strings[1]?.includes('?disabled='))
    .map(item=>({value:item.values[0],disabled:item.values[1],checked:item.values[2]==='true'}));
}
const choice=(page,value)=>choices(page).find(item=>item.value===value);
const reads=f=>f.calls.filter(call=>call.method.endsWith('.get'));
const emit=(f,detail)=>f.listeners.get('mirally-product-event')({detail});

test('composer choices load the effective policy and save only to the current session',async()=>{
  let policy=permission();
  const f=fixture((method,params)=>{
    if(method==='action.permissions.set') return policy=permission(params.enabled);
    if(method==='action.permissions.get') return policy;
    if(method==='interaction.plan.get') return plan();
    throw new Error(`Unexpected ${method}`);
  });
  f.page.sessionKey='agent:main:one'; await f.page.refresh();
  assert.deepEqual(plain(f.page.permission),permission());
  assert.equal(choice(f.page,'ask').checked,true);
  assert.equal(choice(f.page,'session').checked,false);
  assert.equal(choice(f.page,'execute').checked,true);
  await f.page.change('permission',true); await settle();
  assert.equal(f.page.automatic,true);
  assert.equal(choice(f.page,'session').checked,true);
  assert.equal(choice(f.page,'ask').checked,false);
  assert.deepEqual(plain(f.calls.find(call=>call.method==='action.permissions.set').params),{conversation_id:'one',enabled:true});
  assert.equal(f.calls.some(call=>call.method.startsWith('action.approvals.')),false);
});

test('global trust shows the effective automatic policy without offering an ineffective session override',async()=>{
  const f=fixture(method=>method==='action.permissions.get'?permission(false,true):plan());
  f.page.sessionKey='agent:main:one'; await f.page.refresh();
  assert.equal(f.page.permission.session_auto_approve,false);
  assert.equal(f.page.automatic,true);
  assert.equal(choice(f.page,'ask'),undefined);
  assert.equal(choice(f.page,'session'),undefined);
  const rendered=stringify(f.page.render());
  assert.match(rendered,/aria-label=会话模式与权限：执行，自动批准/);
  assert.match(rendered,/自动批准 · 全局设置/);
  await f.page.select('ask'); await f.page.change('permission',true);
  assert.equal(f.calls.some(call=>call.method==='action.permissions.set'),false);
  await f.page.select('settings');
  assert.deepEqual(f.navigation,['settings']);
});

test('a permission mutation uses the returned effective policy rather than the requested boolean',async()=>{
  let policy=permission();
  const f=fixture(method=>{
    if(method==='action.permissions.set') return policy=permission(false,true);
    return method==='action.permissions.get'?policy:plan();
  });
  f.page.sessionKey='agent:main:one'; await f.page.refresh();
  await f.page.change('permission',true); await settle();
  assert.deepEqual(plain(f.page.permission),permission(false,true));
  assert.equal(f.page.automatic,true);
  assert.equal(choice(f.page,'session'),undefined);
});

test('a save response for the previous session cannot replace the new session policy',async()=>{
  const savingResult=deferred();
  const f=fixture((method,params)=>method==='action.permissions.set'?savingResult.promise:
    method==='action.permissions.get'?permission(params.conversation_id==='one'):plan());
  f.page.sessionKey='agent:main:one'; await f.page.refresh();
  const saving=f.page.change('permission',false);
  assert.equal(f.page.busy,true);
  f.page.sessionKey='agent:main:two'; f.page.updated(new Map([['sessionKey','agent:main:one']]));
  await settle();
  assert.equal(f.page.busy,false);
  assert.equal(f.page.automatic,false);
  savingResult.resolve(permission(true,true)); await saving; await settle();
  assert.deepEqual(plain(f.page.permission),permission());
  assert.equal(f.page.busy,false);
  assert.equal(choice(f.page,'ask').checked,true);
  assert.deepEqual(plain(f.calls.find(call=>call.method==='action.permissions.set').params),{conversation_id:'one',enabled:false});
});

test('older reads cannot replace a newer refresh within the same session',async()=>{
  const oldPermission=deferred(),oldPlan=deferred();let permissionReads=0,planReads=0;
  const f=fixture(method=>method==='action.permissions.get'?(++permissionReads===1?oldPermission.promise:permission()):
    (++planReads===1?oldPlan.promise:plan()));
  f.page.sessionKey='agent:main:one'; const oldRead=f.page.refresh(); await settle();
  await f.page.refresh();
  assert.equal(f.page.loading,false);
  oldPermission.resolve(permission(true,true));oldPlan.resolve(plan(true));await oldRead;
  assert.deepEqual(plain(f.page.permission),permission());
  assert.deepEqual(plain(f.page.plan),plan());
  assert.equal(choice(f.page,'execute').checked,true);
});

test('a read started before saving cannot overwrite the pending action or its result',async()=>{
  const oldPermission=deferred(),savingResult=deferred();let permissionReads=0,policy=permission();
  const f=fixture((method,params)=>{
    if(method==='action.permissions.get') return ++permissionReads===2?oldPermission.promise:policy;
    if(method==='action.permissions.set') return savingResult.promise.then(()=>policy=permission(params.enabled));
    return plan();
  });
  f.page.sessionKey='agent:main:one';await f.page.refresh();
  const oldRead=f.page.refresh();await settle();
  const saving=f.page.change('permission',true);
  oldPermission.resolve(permission(false,true));await oldRead;
  assert.equal(f.page.busy,true);
  assert.deepEqual(plain(f.page.permission),permission());
  assert.equal(choice(f.page,'ask').disabled,true);
  savingResult.resolve();await saving;await settle();
  assert.equal(f.page.busy,false);
  assert.deepEqual(plain(f.page.permission),permission(true));
  assert.equal(choice(f.page,'session').checked,true);
});

test('permission events during saving and duplicate choices do not duplicate the mutation or strand busy state',async()=>{
  const result=deferred();let policy=permission();
  const f=fixture((method,params)=>{
    if(method==='action.permissions.set') return result.promise.then(()=>policy=permission(params.enabled));
    return method==='action.permissions.get'?policy:plan();
  });
  f.page.sessionKey='agent:main:one';f.page.connectedCallback();await f.page.refresh();
  const before=reads(f).length,saving=f.page.change('permission',true);
  emit(f,{action:'permission-changed',conversation_id:'one'});
  await f.page.change('permission',false);
  await f.page.change('plan',true);
  assert.equal(reads(f).length,before);
  assert.equal(f.calls.filter(call=>call.method.endsWith('.set')).length,1);
  result.resolve();await saving;await settle();
  assert.equal(f.page.busy,false);
  assert.equal(f.page.automatic,true);
  assert.equal(reads(f).length,before+2);
  const after=reads(f).length;
  emit(f,{action:'permission-changed',conversation_id:'elsewhere'});await settle();
  assert.equal(reads(f).length,after);
});

test('a rejected permission change keeps the confirmed policy and offers another attempt',async()=>{
  let fail=true,policy=permission();
  const f=fixture((method,params)=>{
    if(method==='action.permissions.set') {if(fail) throw new Error('保存失败');return policy=permission(params.enabled);}
    return method==='action.permissions.get'?policy:plan();
  });
  f.page.sessionKey='agent:main:one';await f.page.refresh();
  await f.page.change('permission',true);await settle();
  assert.equal(f.page.busy,false);
  assert.equal(f.page.automatic,false);
  assert.equal(choice(f.page,'ask').checked,true);
  assert.equal(f.page.error,'保存失败');
  assert.match(stringify(f.page.render()),/role="alert"/);
  fail=false;await f.page.select('session');await settle();
  assert.equal(f.page.error,'');
  assert.equal(f.page.automatic,true);
});

test('unknown permissions are never presented as ask or automatic, and retry restores available choices',async()=>{
  let unavailable=true;
  const f=fixture(method=>{
    if(method==='action.permissions.get') {if(unavailable) throw new Error('host starting');return permission();}
    return plan();
  });
  f.page.sessionKey='agent:main:one';
  assert.equal(f.page.loading,true);
  assert.doesNotMatch(stringify(f.page.render()),/value="retry"/);
  await f.page.refresh();
  assert.equal(f.page.loading,false);
  assert.equal(f.page.permission,null);
  assert.equal(choice(f.page,'ask'),undefined);
  assert.equal(choice(f.page,'session'),undefined);
  assert.match(stringify(f.page.render()),/value="retry"/);
  await f.page.change('permission',true);
  assert.equal(f.calls.some(call=>call.method==='action.permissions.set'),false);
  unavailable=false;await f.page.select('retry');
  assert.deepEqual(plain(f.page.permission),permission());
  assert.equal(choice(f.page,'ask').checked,true);
  assert.doesNotMatch(stringify(f.page.render()),/value="retry"/);
});

test('a pending plan choice and the active mode stay distinct until the interaction event applies it',async()=>{
  let current=plan();
  const f=fixture((method,params)=>{
    if(method==='interaction.plan.set') return current=plan(false,params.active);
    return method==='action.permissions.get'?permission():current;
  });
  f.page.sessionKey='agent:main:one';f.page.connectedCallback();await f.page.refresh();
  await f.page.select('plan');await settle();
  assert.deepEqual(plain(f.page.plan),plan(false,true));
  assert.equal(choice(f.page,'plan').checked,true);
  assert.equal(choice(f.page,'execute').checked,false);
  assert.match(stringify(f.page.render()),/计划（下轮）/);
  assert.match(stringify(f.page.render()),/当前执行/);
  assert.deepEqual(plain(f.calls.find(call=>call.method==='interaction.plan.set').params),{conversation_id:'one',active:true});
  current=plan(true);emit(f,{type:'interaction',conversation_id:'one'});await settle();
  assert.deepEqual(plain(f.page.plan),plan(true));
  assert.doesNotMatch(stringify(f.page.render()),/（下轮）/);
  await f.page.select('execute');await settle();
  assert.equal(choice(f.page,'execute').checked,true);
  assert.equal(f.calls.filter(call=>call.method==='interaction.plan.set').at(-1).params.active,false);
});

test('plan changes are unavailable without a native mode owner and failure retains the active choice',async()=>{
  let available=false;
  const f=fixture(method=>{
    if(method==='interaction.plan.set') throw new Error('模式保存失败');
    return method==='action.permissions.get'?permission():{available,active:false};
  });
  f.page.sessionKey='agent:main:one';await f.page.refresh();await f.page.select('plan');
  assert.equal(choice(f.page,'plan'),undefined);
  assert.equal(f.calls.some(call=>call.method==='interaction.plan.set'),false);
  available=true;await f.page.refresh();await f.page.select('plan');await settle();
  assert.equal(f.page.busy,false);
  assert.equal(f.page.plan.active,false);
  assert.equal(choice(f.page,'execute').checked,true);
  assert.equal(f.page.error,'模式保存失败');
});

test('hidden or disconnected controls do not read or accept a late policy',async()=>{
  const result=deferred();
  const f=fixture(method=>method==='action.permissions.get'?result.promise:plan());
  await f.page.refresh();assert.equal(f.calls.length,0);
  f.page.sessionKey='agent:main:one';f.page.presented=false;await f.page.refresh();
  assert.equal(f.calls.length,0);assert.equal(stringify(f.page.render()),'');
  f.page.presented=true;f.page.connectedCallback();const pending=f.page.refresh();await settle();
  f.page.disconnectedCallback();f.page.isConnected=false;
  result.resolve(permission(true,true));await pending;
  assert.equal(f.page.permission,null);
  assert.equal(f.listeners.size,0);
});

test('pending actions retain their exact details and one-shot decision buttons separately from configuration',async()=>{
  const action={id:'a',conversation_id:'one',title:'运行实验',detail:'python experiment.py --output results'};
  let pending=[action];const result=deferred();
  const f=fixture(method=>{
    if(method==='action.approvals.list') return {approvals:pending};
    if(method==='action.approvals.decide') return result.promise.then(()=>{pending=[];});
    throw new Error(`Unexpected ${method}`);
  },{file:'action-approvals',className:'MirallyActionApprovals'});
  f.page.sessionKey='agent:main:one';await f.page.refresh();
  assert.deepEqual(plain(f.page.approvals),[action]);
  const rendered=stringify(f.page.render());
  assert.match(rendered,/role="region"/);assert.match(rendered,/python experiment.py --output results/);
  assert.match(rendered,/允许这次/);assert.match(rendered,/拒绝/);
  assert.doesNotMatch(rendered,/wa-dropdown|type="checkbox"/);
  const saving=f.page.decide(f.page.approvals[0],true);
  await f.page.decide(f.page.approvals[0],false);
  assert.equal(f.page.busy,true);
  assert.equal(f.calls.filter(call=>call.method==='action.approvals.decide').length,1);
  assert.deepEqual(plain(f.calls.find(call=>call.method==='action.approvals.decide').params),{approval_id:'a',conversation_id:'one',allow:true});
  result.resolve();await saving;await settle();
  assert.equal(f.page.busy,false);assert.deepEqual(plain(f.page.approvals),[]);
});

test('an older pending-action read cannot resurrect an action after the decision refresh removes it',async()=>{
  const action={id:'a',conversation_id:'one',title:'运行实验',detail:'python experiment.py'};
  const oldResult=deferred();let pending=[action],listReads=0;
  const f=fixture(method=>{
    if(method==='action.approvals.list') return ++listReads===2?oldResult.promise:{approvals:pending};
    if(method==='action.approvals.decide') {pending=[];return {};}
    throw new Error(`Unexpected ${method}`);
  },{file:'action-approvals',className:'MirallyActionApprovals'});
  f.page.sessionKey='agent:main:one';await f.page.refresh();
  const oldRead=f.page.refresh();await settle();
  await f.page.decide(f.page.approvals[0],true);await settle();
  assert.equal(listReads,3);
  assert.deepEqual(plain(f.page.approvals),[]);
  oldResult.resolve({approvals:[action]});await oldRead;
  assert.deepEqual(plain(f.page.approvals),[]);
  assert.doesNotMatch(stringify(f.page.render()),/role="region"|experiment\.py/);
});

test('a failed decision for the previous session cannot show an error or release a new session decision',async()=>{
  const oldResult=deferred(),newResult=deferred();
  const action=id=>({id:`action-${id}`,conversation_id:id,title:`动作 ${id}`,detail:`command-${id}`});
  let newPending=true;
  const f=fixture((method,params)=>{
    if(method==='action.approvals.list') return {approvals:params.conversation_id==='one'?[action('one')]:newPending?[action('two')]:[]};
    if(method==='action.approvals.decide') return params.conversation_id==='one'?oldResult.promise:newResult.promise.then(()=>{newPending=false;});
    throw new Error(`Unexpected ${method}`);
  },{file:'action-approvals',className:'MirallyActionApprovals'});
  f.page.sessionKey='agent:main:one';await f.page.refresh();
  const oldDecision=f.page.decide(f.page.approvals[0],true);
  f.page.sessionKey='agent:main:two';f.page.updated(new Map([['sessionKey','agent:main:one']]));await settle();
  const newDecision=f.page.decide(f.page.approvals[0],false);
  oldResult.reject(new Error('旧会话审批失败'));await oldDecision;
  assert.equal(f.page.error,'');
  assert.equal(f.page.busy,true);
  assert.equal(f.page.approvals[0].conversation_id,'two');
  assert.doesNotMatch(stringify(f.page.render()),/旧会话审批失败|command-one/);
  newResult.resolve();await newDecision;await settle();
  assert.equal(f.page.busy,false);
  assert.deepEqual(plain(f.page.approvals),[]);
  assert.deepEqual(plain(f.calls.filter(call=>call.method==='action.approvals.decide').map(call=>call.params)),[
    {approval_id:'action-one',conversation_id:'one',allow:true},
    {approval_id:'action-two',conversation_id:'two',allow:false},
  ]);
});

test('native answers preserve drafts across polling and a failed submit, then send the complete answer once',async()=>{
  const request={id:'request',conversation_id:'one',state:'open',can_reply:true,questions:[{id:'question',question:'选择实验参数',multiSelect:true,options:[{label:'A'},{label:'B'}]}]};
  let fail=true,pending=[request];const submitted=[];
  const f=fixture((method,params)=>{
    if(method==='interaction.questions.list') return {questions:pending};
    if(method==='interaction.questions.answer') {submitted.push(plain(params));if(fail) throw new Error('暂未送达');pending=[];return {};}
    throw new Error(`Unexpected ${method}`);
  },{file:'native-interactions',className:'MirallyNativeInteractions'});
  f.page.sessionKey='agent:main:one';await f.page.refresh();
  await f.page.answer(request);assert.equal(submitted.length,0);
  const question=request.questions[0];
  f.page.select(request,question,'A',true);f.page.select(request,question,'B',true);f.page.select(request,question,'A',false);
  f.page.write(request,question,'保留这份参数说明');await f.page.refresh();
  assert.deepEqual(plain(f.page.draft(request,question)),{id:'question',selected:['B'],custom:'保留这份参数说明'});
  await f.page.answer(request);
  assert.equal(f.page.error,'暂未送达');assert.equal(f.page.submitting,'');
  assert.equal(f.page.draft(request,question).custom,'保留这份参数说明');
  fail=false;await f.page.answer(request);
  assert.deepEqual(submitted[1],{conversation_id:'one',question_id:'request',answers:[{id:'question',selected:['B'],custom:'保留这份参数说明'}]});
  assert.equal(f.page.error,'');assert.equal(f.page.drafts.size,0);assert.deepEqual(plain(f.page.requests),[]);
});

test('an older question read cannot resurrect an answered request after its newer empty refresh',async()=>{
  const request={id:'request',conversation_id:'one',state:'open',can_reply:true,questions:[{id:'question',question:'实验输出位置？'}]};
  const oldResult=deferred();let pending=[request],listReads=0;
  const f=fixture(method=>{
    if(method==='interaction.questions.list') return ++listReads===2?oldResult.promise:{questions:pending};
    if(method==='interaction.questions.answer') {pending=[];return {};}
    throw new Error(`Unexpected ${method}`);
  },{file:'native-interactions',className:'MirallyNativeInteractions'});
  f.page.sessionKey='agent:main:one';await f.page.refresh();
  f.page.write(request,request.questions[0],'results/experiment');
  const oldRead=f.page.refresh();await settle();
  await f.page.answer(request);
  assert.equal(listReads,3);
  assert.deepEqual(plain(f.page.requests),[]);
  assert.equal(f.page.drafts.size,0);
  oldResult.resolve({questions:[request]});await oldRead;
  assert.deepEqual(plain(f.page.requests),[]);
  assert.equal(f.page.drafts.size,0);
  assert.doesNotMatch(stringify(f.page.render()),/role="region"|实验输出位置/);
});
