const {test} = require('node:test');
const assert = require('node:assert/strict');
const office = require('../openassist/assets/control-ui-office.js');

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
test('work requests carry the selected task and honest handoff requirements', () => {
  assert.equal(office.workflows.length,10);
  for(const item of office.workflows) {
    assert.match(office.compose(item.id,'整理这些材料'), /材料/);
    assert.match(office.compose(item.id,'整理这些材料'), /## 交接/);
    assert.match(office.compose(item.id,'整理这些材料'), /实际执行结果/);
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
  assert.equal(office.isFinal({...message,channel:'commentary',__openclaw:{runTerminal:true}}),false);
  assert.equal(office.isFinal({...message,phase:'commentary',stopReason:'stop'}),false);
  assert.equal(office.isFinal({...message,stopReason:'stop',content:[{type:'text',text:'正在处理',textSignature:'{"v":1,"phase":"commentary"}'}]}),false);
  assert.equal(office.isFinal({...message,stopReason:'stop',content:[{type:'text',text:'已回复',textSignature:'{"v":1,"phase":"final_answer"}'}]}),true);
  assert.equal(office.isFinal({...message,__openclaw:{runTerminal:true}}),true);
  assert.equal(office.isFinal({...message,stopReason:'stop'}),true);
  assert.equal(office.isFinal({...message,stopReason:'stop',content:'NO_REPLY'}),false);
});
