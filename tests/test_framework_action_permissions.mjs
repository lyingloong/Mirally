import {test} from 'node:test';
import assert from 'node:assert/strict';
import {MirallyProduct} from '../kernel/dsh/packages/mirally/mirally-product/src/index.mjs';

function fixture() {
  const settings={action_approval_mode:'ask',weixin_auto_approve_enabled:false};
  const conversation={conversation_id:'conversation',generation:1,archived:false,frozen:false};
  const agent={id:'native'};
  const product=Object.assign(Object.create(MirallyProduct.prototype),{
    sessionActionGrants:new Map(),nativeConversations:new Map([['native',conversation]]),
    handles:new Map([['native',{agent}]]),consumedCommandId:()=> 'command',
    commandPolicy:()=> ({origin:'desktop'}),conversation:async()=>conversation,
    rpc:async method=>{assert.equal(method,'settings.get');return {...settings};},broadcast:()=>{},
  });
  return {product,settings,conversation};
}

test('session grants are scoped to the conversation generation and independent of the default',async()=>{
  const {product,settings,conversation}=fixture();
  const scope={conversationId:'conversation',sessionId:'native',commandId:'command'};
  assert.equal(await product.actionPermission(scope),false);
  await product.publicRpc('action.permissions.set',{conversation_id:'conversation',enabled:true});
  assert.equal(await product.actionPermission(scope),true);
  assert.equal(await product.actionPermission({...scope,conversationId:'other'}),false);
  conversation.generation=2;
  assert.equal(await product.actionPermission(scope),false);
  assert.equal((await product.publicRpc('action.permissions.get',{conversation_id:'conversation'})).session_auto_approve,false);
  settings.action_approval_mode='trusted';
  assert.equal(await product.actionPermission(scope),true);
  assert.equal((await product.publicRpc('action.permissions.get',{conversation_id:'conversation'})).default_trusted,true);
  settings.action_approval_mode='ask';
  assert.equal(await product.actionPermission(scope),false);
});

test('WeChat inherits automatic approval only when explicitly enabled',async()=>{
  const {product,settings}=fixture();
  product.commandPolicy=()=>({origin:'weixin'});
  const scope={conversationId:'conversation',sessionId:'native',commandId:'command'};
  settings.action_approval_mode='trusted';
  assert.equal(await product.actionPermission(scope),false);
  settings.weixin_auto_approve_enabled=true;
  assert.equal(await product.actionPermission(scope),true);
  settings.weixin_auto_approve_enabled=false;
  assert.equal(await product.actionPermission(scope),false);
});
