import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const manifest=JSON.parse(await fs.readFile(path.join(root,'upstream-files.json'),'utf8'));
const maintained=new Set(['ui/src/app/bootstrap.ts','ui/src/main.ts',
  'ui/src/lib/chat/chat-types.ts','ui/src/lib/chat/outbox-store-codec.ts',
  'ui/src/pages/chat/chat-send-submit.ts','ui/src/pages/chat/chat-send-delivery.ts',
  'ui/src/pages/chat/chat-send-request.ts']);
let checked=0;
for(const [name,identity] of Object.entries(manifest.files)) {
  const bytes=await fs.readFile(path.join(root,name));
  const hash=createHash('sha1').update(Buffer.from(`blob ${bytes.length}\0`)).update(bytes).digest('hex');
  if(hash!==identity.gitBlob&&!maintained.has(name))throw new Error('Unexpected upstream source modification: '+name);
  checked++;
}
const locales=JSON.parse(await fs.readFile(path.join(root,'locales/upstream.json'),'utf8'));
if(locales.source.commit!==manifest.commit)throw new Error('Locale and component releases differ');
for(const [locale,identity]of Object.entries(locales.files)){
  const bytes=await fs.readFile(path.join(root,'locales',locale+'.json'));
  if(createHash('sha256').update(bytes).digest('hex')!==identity.sha256)throw new Error('Changed upstream locale: '+locale);
}
console.log(`Verified ${checked} pinned upstream files, ${maintained.size} documented integration seams, ${Object.keys(locales.files).length} original locale catalogs`);
