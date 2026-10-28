import {test} from 'node:test';
import assert from 'node:assert/strict';
import {continuationHistory} from '../kernel/dsh/packages/mirally/mirally-product/src/continuation.mjs';

test('continuation retains earlier user text but does not replay tool-derived assistant output',()=>{
  const messages=[
    {generation:1,role:'user',command_id:'one',text:'Run the experiment',content:[{type:'text'}]},
    {generation:1,role:'assistant',command_id:'one',text:'Private tool-derived output'},
    {generation:1,role:'toolResult',command_id:'one',text:''},
    {generation:2,role:'user',command_id:'two',text:'What next?',content:[{type:'text'}]},
    {generation:2,role:'assistant',command_id:'two',text:'Review the result'},
    {generation:3,role:'user',command_id:'three',text:'Current message'},
  ];
  const result=continuationHistory({messages,gaps:[]},[1,2]);
  assert.deepEqual(result.messages.map(item=>item.text),['Run the experiment','What next?','Review the result']);
  assert.equal(result.incomplete,true);
  assert.match(result.instruction,/stopped turn may be incomplete/);
});

test('continuation respects eligible generations and bounds context',()=>{
  const messages=Array.from({length:30},(_,i)=>({generation:1,role:'user',text:`${i}:`+'a'.repeat(5000),content:[{type:'text'}]}));
  const result=continuationHistory({messages,gaps:[]},[1]);
  assert.ok(result.messages.reduce((sum,m)=>sum+Array.from(m.text).length,0)<=16000);
  assert.equal(result.incomplete,true);
  assert.deepEqual(continuationHistory({messages,gaps:[]},[]).messages,[]);
});
