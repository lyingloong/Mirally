import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';

const source=readFileSync(new URL('../frontend/control-ui/ui/src/lib/sessions/navigation.ts',import.meta.url),'utf8');
const match=source.match(/export function compareSessionRowsByUpdatedAt\([\s\S]*?\n}\n/);
if(!match)throw Error('conversation ordering function is missing');
const {compareSessionRowsByUpdatedAt}=await import('data:text/javascript;base64,'+Buffer.from(stripTypeScriptTypes(match[0],{mode:'transform'})).toString('base64'));

test('pinned and ordinary conversations each use descending activity, not pin order',()=>{
  const rows=[{key:'unpinned-new',updatedAt:90},{key:'pinned-old',pinned:true,pinnedAt:80,updatedAt:10},
    {key:'pinned-new',pinned:true,pinnedAt:20,updatedAt:50},{key:'unpinned-old',updatedAt:5}];
  assert.deepEqual(rows.toSorted(compareSessionRowsByUpdatedAt).map(row=>row.key),
    ['pinned-new','pinned-old','unpinned-new','unpinned-old']);
});
