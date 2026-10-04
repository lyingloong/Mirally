const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readDrop} = require('../openassist/assets/control-ui-intake.js');

const drop = values => ({getData: type => values[type] ?? ''});

test('URI intake accepts web links, ignores comments, unsafe schemes and credentials', () => {
  const result = readDrop(drop({'text/uri-list':
    '# source\r\nhttps://example.test/report\r\njavascript:alert(1)\r\nfile:///C:/secret\r\ndata:text/html,test\r\nhttps://user:pass@example.test\r\nhttps://example.test/report'}));
  assert.deepEqual(result, {files: [], urls: ['https://example.test/report'], text: ''});
});

test('files take precedence over accompanying local paths, URI lists and HTML', () => {
  const file = {name: 'report.pdf'};
  const transfer = {...drop({'text/plain': 'C:\\private\\report.pdf',
    'text/uri-list': 'https://example.test', 'text/html': '<img src="https://example.test">'}), files: [file]};
  assert.deepEqual(readDrop(transfer), {files: [file], urls: [], text: ''});
});

test('clipboard file items work when files is empty; HTML is never consumed', () => {
  const image = {name: 'clipboard.png', type: 'image/png'};
  assert.deepEqual(readDrop({files: [], items: [{kind: 'file', getAsFile: () => image}]}),
    {files: [image], urls: [], text: ''});
  assert.deepEqual(readDrop(drop({'text/html': '<script>bad()</script>', 'text/plain': '  Notes  '})),
    {files: [], urls: [], text: 'Notes'});
});
