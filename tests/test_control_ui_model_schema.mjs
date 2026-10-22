import {test} from 'node:test';
import assert from 'node:assert/strict';
import {verifyModelSchema} from '../tools/verify_model_schema.mjs';

test('model settings fields use the installed native OpenClaw schema, with no invented task keys',async()=>{
  const report=await verifyModelSchema();
  assert.ok(report.passed>=26);
  assert.equal(report.offline,true);
  assert.equal(report.privateConfigTouched,false);
});
