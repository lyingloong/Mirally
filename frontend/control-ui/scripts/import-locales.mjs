// Maintenance helper: materialize the exact release's static locale objects.
// No gateway, DOM, user configuration or model is loaded.
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { pathToFileURL, fileURLToPath } from 'node:url';

const source = process.argv[2];
if (!source) throw new Error('Pass an explicit OpenClaw 2026.9.6 package directory');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const info = JSON.parse(await fs.readFile(path.join(source, 'dist/build-info.json'), 'utf8'));
if (info.version !== '2026.9.6' || info.commit !== 'eb377ac59e6c9fd6c7705028034812becf00271b') throw new Error('Expected the pinned frontend release');
const assets = path.join(source, 'dist/control-ui/assets');
const names = await fs.readdir(assets);
const locales = ['zh-CN','zh-TW','pt-BR','de','es','ja-JP','ko','fr','hi','ar','it','tr','uk','id','pl','th','vi','nl','fa','ru'];
const provenance = { source: info, files: {} };
await fs.mkdir(path.join(root, 'locales'), {recursive:true});
for (const locale of locales) {
  const name = names.find(name => new RegExp(`^${locale}-[\\w-]+\\.js$`).test(name));
  if (!name) throw new Error(`Missing release locale ${locale}`);
  const module = await import(pathToFileURL(path.join(assets, name)));
  const catalog = module[locale.replaceAll('-', '_')];
  if (!catalog || typeof catalog !== 'object') throw new Error(`Missing locale export ${name}`);
  const bytes = JSON.stringify(catalog) + '\n';
  await fs.writeFile(path.join(root, 'locales', locale + '.json'), bytes);
  provenance.files[locale] = { sourceChunk: name, sha256: createHash('sha256').update(bytes).digest('hex') };
}
await fs.writeFile(path.join(root,'locales','upstream.json'), JSON.stringify(provenance,null,2)+'\n');
console.log(`Materialized ${locales.length} exact-release locale catalogs`);
