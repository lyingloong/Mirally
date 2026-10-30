import test from 'node:test';
import assert from 'node:assert/strict';
import {installAssistantTools} from '../kernel/dsh/packages/mirally/mirally-product/src/assistant-tools.mjs';

test('stopping an assistant turn also cancels its owned target turn', async()=>{
  const tools=new Map(),controller=new AbortController(),policy={origin:'weixin',installation_id:'installation',command_id:'source',conversation_id:'assistant'};
  const owner={rootSessionId:'agent',commandId:'source',conversation:{conversation_id:'assistant'}};
  let release,started,cancelled;
  const pending=new Promise(resolve=>release=resolve),begun=new Promise(resolve=>started=resolve);
  const host={ctx:{tools:{register:tool=>tools.set(tool.name,tool)}},nativeRuntime:{owner:()=>owner},commandPolicy:()=>policy,
    checkCommandScope:async()=>{},guard:async()=>{},weixin:{authorize:async()=>({mode:'assistant',assistant_conversation_id:'assistant'})},
    consumedCommandId:()=>owner.commandId,conversation:async id=>({conversation_id:id,title:'实验',generation:1}),
    sendWeixinDelegation:async()=>({accepted:true}),finishWeixin:async()=>{started();return pending;},
    cancel:async(id,command)=>{cancelled={id,command};release({state:'cancelled'});}};
  installAssistantTools(host);
  const result=tools.get('mirally_delegate_conversation').execute({conversation_id:'target',instruction:'继续'}, {agent:{id:'agent'},signal:controller.signal});
  const rejected=assert.rejects(result);await begun;controller.abort();await rejected;
  assert.equal(cancelled.id,'target');assert.match(cancelled.command,/^weixin-delegate-/);
});

test('assistant reads reject a target that changes while its history or status is loading', async()=>{
  for (const toolName of ['mirally_conversation_history','mirally_conversation_status']) {
    for (const changed of [{generation:2},{archived:true},{frozen:true}]) {
      const tools=new Map(),policy={origin:'weixin',installation_id:'installation',command_id:'source',conversation_id:'assistant'};
      const owner={rootSessionId:'agent',commandId:'source',conversation:{conversation_id:'assistant'}};
      let target={conversation_id:'target',title:'实验',generation:1,session_id:'session'};
      const change=()=>{target={...target,...changed};};
      const host={ctx:{tools:{register:tool=>tools.set(tool.name,tool)},get:()=>null},nativeRuntime:{owner:()=>owner},commandPolicy:()=>policy,
        checkCommandScope:async()=>{},guard:async()=>{},weixin:{authorize:async()=>({mode:'assistant',assistant_conversation_id:'assistant'})},
        consumedCommandId:()=>owner.commandId,conversation:async()=>({...target}),summary:conversation=>conversation,
        history:async()=>{change();return {messages:[{role:'user',text:'private'}]};},
        workRequest:async()=>{change();return {works:[]};}};
      installAssistantTools(host);
      await assert.rejects(tools.get(toolName).execute({conversation_id:'target'}, {agent:{id:'agent'},signal:new AbortController().signal}),/目标会话已变化|目标会话已归档或不可用/);
    }
  }
});
