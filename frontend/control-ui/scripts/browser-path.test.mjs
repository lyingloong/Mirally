import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import browser from '../ui/src/lib/mirally-browser-path.ts';

test('original filename operations preserve Windows, POSIX, Unicode and dotfile behavior',()=>{
  const samples=['报告.docx','C:\\Work Files\\中文材料.pdf','/tmp/picture.png','.env','.env.local','..','.','a.','a..','archive.tar.gz','folder/','C:\\folder\\','name\\part.txt','https://x.test/materials.csv','C:','C:report.md',''];
  for(const sample of samples){
    const original=path.win32.basename(path.posix.basename(sample));
    const adapted=browser.win32.basename(browser.posix.basename(sample));
    assert.equal(adapted,original,sample);
    assert.equal(browser.extname(adapted),path.win32.extname(original),sample);
    assert.equal(browser.parse(adapted).name,path.win32.parse(original).name,sample);
    assert.equal(browser.posix.extname(sample),path.posix.extname(sample),sample);
  }
});
