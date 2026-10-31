const {test} = require('node:test');
const assert = require('node:assert/strict');
const office = require('../mirally/assets/control-ui-office.js');

const replyFrame = overrides => ({scope:'pane:session',turn:'user:1',reply:null,completion:null,
  working:false,waiting:false,error:null,loading:false,...overrides});
const finalReply = (key='answer:1',text='你好呀！') => ({key,text});
const done = runId => ({phase:'done',sessionKey:'session',runId,occurredAt:123});

test('the retained companion and work surfaces share labels for the real Mirally tools', () => {
  assert.equal(office.toolLabel('mirally_context'), '整理相关记忆与活动');
  assert.equal(office.toolLabel('mirally_preferences'), '读取你的偏好');
  assert.equal(office.toolLabel('mirally_reminders'), '查看提醒');
  assert.equal(office.toolLabel('mirally_set_preference'), '保存你的偏好');
  assert.equal(office.toolLabel('mirally_create_reminder'), '创建提醒');
  assert.equal(office.toolLabel('mirally_create_file'), '保存交付文件');
  assert.equal(office.toolLabel('read'), '读取资料');
  assert.equal(office.toolLabel('constructor'), '处理工作材料');
  assert.equal(office.toolLabel('unconfigured_plugin_tool'), '处理工作材料');
});

test('reply tracker baselines old history and remembers it across session switches', () => {
  const track=office.createReplyTracker(), state=replyFrame({reply:finalReply()});
  assert.equal(track(state),null);
  assert.equal(track(replyFrame({scope:'other',reply:finalReply('other')})),null);
  assert.equal(track(state),null);
});
test('a new final reply notifies even without a published terminal event', () => {
  const track=office.createReplyTracker(); track(replyFrame());
  const result=track(replyFrame({reply:finalReply()}));
  assert.equal(result.text,'你好呀！');
  assert.equal(track(replyFrame({reply:finalReply()})),null);
});
test('final reply while working stays pending until work really stops', () => {
  const track=office.createReplyTracker(); track(replyFrame({working:true}));
  assert.equal(track(replyFrame({working:true,reply:finalReply()})),null);
  assert.equal(track(replyFrame({reply:finalReply()})).text,'你好呀！');
});
test('waiting for a decision does not consume a completed reply', () => {
  const track=office.createReplyTracker(); track(replyFrame());
  assert.equal(track(replyFrame({waiting:true,reply:finalReply()})),null);
  assert.equal(track(replyFrame({reply:finalReply()})).text,'你好呀！');
});
test('hydrating history is not a delivery and later replies still notify', () => {
  const track=office.createReplyTracker(); track(replyFrame({loading:true}));
  assert.equal(track(replyFrame({loading:true,reply:finalReply('old')})),null);
  assert.equal(track(replyFrame({reply:finalReply('old')})),null);
  assert.equal(track(replyFrame({reply:finalReply('new')})).text,'你好呀！');
});
test('initial hydration may restore the user turn and old terminal without a notification', () => {
  const track=office.createReplyTracker(); track(replyFrame({turn:'empty',loading:true}));
  assert.equal(track(replyFrame({loading:true,reply:finalReply('old'),completion:done('old-run')})),null);
  assert.equal(track(replyFrame({reply:finalReply('old'),completion:done('old-run')})),null);
});
test('delayed initial hydration after an empty pane does not announce restored history', () => {
  const track=office.createReplyTracker(); track(replyFrame({turn:'empty'}));
  assert.equal(track(replyFrame({turn:'empty',loading:true})),null);
  assert.equal(track(replyFrame({loading:true,reply:finalReply('old'),completion:done('old-run')})),null);
  assert.equal(track(replyFrame({reply:finalReply('old'),completion:done('old-run')})),null);
});
test('idle history refresh after delivery does not replay a restored answer', () => {
  const track=office.createReplyTracker(); track(replyFrame());
  track(replyFrame({working:true}));
  assert.equal(track(replyFrame({reply:finalReply()})).text,'你好呀！');
  assert.equal(track(replyFrame({loading:true,reply:finalReply('restored-id')})),null);
  assert.equal(track(replyFrame({reply:finalReply('restored-id')})),null);
});
test('completion history refresh pauses delivery without consuming final or terminal', () => {
  const track=office.createReplyTracker(); track(replyFrame({working:true}));
  assert.equal(track(replyFrame({loading:true,reply:finalReply(),completion:done('refresh-run')})),null);
  const result=track(replyFrame({reply:finalReply(),completion:done('refresh-run')}));
  assert.equal(result.text,'你好呀！');
  assert.equal(result.key,'session:refresh-run');
});
test('synchronous reply history refresh survives missing terminal publication', () => {
  const track=office.createReplyTracker(); track(replyFrame({reply:finalReply('old')}));
  track(replyFrame({turn:'user:2',reply:finalReply('old')}));
  assert.equal(track(replyFrame({turn:'user:2',loading:true,reply:finalReply('new')})),null);
  assert.equal(track(replyFrame({turn:'user:2',reply:finalReply('new')})).text,'你好呀！');
});
test('same text in different user turns can notify again', () => {
  const track=office.createReplyTracker(); track(replyFrame());
  const first=track(replyFrame({reply:finalReply()}));
  track(replyFrame({turn:'user:2',working:true}));
  const second=track(replyFrame({turn:'user:2',reply:finalReply()}));
  assert.equal(second.text,first.text); assert.notEqual(second.key,first.key);
});
test('an atomic fast user turn and final response change still notifies', () => {
  const track=office.createReplyTracker(); track(replyFrame({reply:finalReply('old')}));
  assert.equal(track(replyFrame({turn:'user:2',reply:finalReply('new')})).text,'你好呀！');
});
test('native done without text yields a fallback and later text updates the same notice', () => {
  const track=office.createReplyTracker(); track(replyFrame());
  const fallback=track(replyFrame({completion:done('run:1')}));
  assert.equal(fallback.text,'');
  assert.equal(track(replyFrame({completion:done('run:1')})),null);
  const update=track(replyFrame({reply:finalReply(),completion:done('run:1')}));
  assert.equal(update.key,fallback.key); assert.equal(update.text,'你好呀！');
  assert.equal(track(replyFrame({reply:finalReply()})),null);
});
test('done does not reuse an older turns reply as the new result', () => {
  const track=office.createReplyTracker(); track(replyFrame({reply:finalReply('old')}));
  const fallback=track(replyFrame({turn:'user:2',reply:finalReply('old'),completion:done('run:2')}));
  assert.equal(fallback.text,'');
});
test('interrupted or error replies are not announced as successful after errors clear', () => {
  for (const issue of [{completion:{phase:'interrupted'}},{error:'failed'}]) {
    const track=office.createReplyTracker(); track(replyFrame());
    assert.equal(track(replyFrame({reply:finalReply(),...issue})),null);
    assert.equal(track(replyFrame({reply:finalReply()})),null);
  }
});

test('ordinary conversation and a quoted handoff do not become a completed task', () => {
  assert.equal(office.handoff('你好！有什么可以帮你？'), null);
  assert.equal(office.handoff('```md\n## 交接\n### 已完成\n生成文件\n```'), null);
});
test('handoff keeps genuine uncertainty and next steps as plain text', () => {
  assert.deepEqual(office.handoff('说明\n## 交接\n### 已完成\n- 已保存 **行动清单**。\n### 待确认\n- 展示日期是12日还是14日？\n### 下一步\n根据确认结果更新 [汇报稿](https://example.test)。'),
    {completed:'- 已保存 行动清单。', attention:'- 展示日期是12日还是14日？', next:'根据确认结果更新 汇报稿。'});
});
test('only the last handoff counts, and later unrelated headings do not leak in', () => {
  const source='## 交接\n### 已完成\n旧结果\n## 其他\n无关\n## 交接\n### 已完成\n最新结果\n### 待确认\n无。\n## 附录\n不属于交接';
  assert.deepEqual(office.handoff(source), {completed:'最新结果',attention:'',next:''});
});
test('unknown and absent sections do not fabricate a completion or decision', () => {
  assert.equal(office.handoff('## 交接\n### 不认识的栏目\n内容'), null);
  assert.deepEqual(office.handoff('## 交接\n### 待确认\n预算尚未获批'), {completed:'',attention:'预算尚未获批',next:''});
});
test('quick starts carry the selected task without forcing a handoff or a file', () => {
  assert.equal(office.workflows.length,10);
  for(const item of office.workflows) {
    assert.match(office.compose(item.id,'整理这些材料'), /材料/);
    assert.doesNotMatch(office.compose(item.id,'整理这些材料'), /## 交接/);
    assert.match(office.compose(item.id,'整理这些材料'), /直接在对话中呈现/);
  }
  assert.equal(office.compose('unknown','目标'), '');
});
test('different work preparations preserve user material, audience and task focus', () => {
  const source='https://example.test/a\nhttps://example.test/b';
  assert.match(office.compose('web','核对两份来源','reply',{source,focus:'核对事实'}),/https:\/\/example.test\/a\nhttps:\/\/example.test\/b/);
  assert.match(office.compose('write','保留原意','document',{source:'原始文稿',audience:'老师',focus:'更清楚'}),/面向对象：老师/);
  assert.match(office.compose('screen','看看错误','reply'),/不是实时控制授权/);
  assert.match(office.compose('visual','提取文字','reply'),/无法查看图片/);
  assert.match(office.compose('brief','准备展示','pages'),/HTML 汇报或演示初稿/);
});
test('work output selection cannot request both saving a document and replying only', () => {
  for(const item of office.workflows) {
    const reply=office.compose(item.id,'给老师看，保留分歧。','reply');
    assert.match(reply,/给老师看，保留分歧/);
    assert.match(reply,/本次不要求保存文件/);
    assert.doesNotMatch(reply,/保存为可编辑/);
    assert.match(office.compose(item.id,'整理材料','table'),/表格或清单/);
  }
  assert.equal(office.compose('unknown','目标'), '');
  assert.equal(office.compose('meeting','目标','unknown'), '');
});
test('intermediate tool narration cannot be handed off as the final reply', () => {
  const message={role:'assistant',content:'我准备整理文件'};
  assert.equal(office.isFinal(message),false);
  assert.equal(office.isFinal({...message,stopReason:'tooluse'}),false);
  assert.equal(office.isFinal({...message,stopReason:'error',__openclaw:{runTerminal:true}}),false);
  assert.equal(office.isFinal({...message,channel:'commentary',__openclaw:{runTerminal:true}}),false);
  assert.equal(office.isFinal({...message,phase:'commentary',stopReason:'stop'}),false);
  assert.equal(office.isFinal({...message,stopReason:'stop',content:[{type:'text',text:'正在处理',textSignature:'{"v":1,"phase":"commentary"}'}]}),false);
  assert.equal(office.isFinal({...message,stopReason:'stop',content:[{type:'text',text:'已回复',textSignature:'{"v":1,"phase":"final_answer"}'}]}),true);
  assert.equal(office.isFinal({...message,__openclaw:{runTerminal:true}}),true);
  assert.equal(office.isFinal({...message,stopReason:'stop'}),true);
  assert.equal(office.isFinal({...message,stopReason:'stop',content:'NO_REPLY'}),false);
});
