import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash, randomUUID } from 'node:crypto';
import { build } from 'vite';
import ts from 'typescript';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const stage=path.join(root,'.dist-stage-'+randomUUID());
const target=path.join(root,'dist');
const retired=path.join(root,'.dist-retired-'+randomUUID());
function owned(directory) {if(path.dirname(directory)!==root || !/^\.dist-(?:stage|retired)-/.test(path.basename(directory)))throw new Error('Unsafe build directory');return directory;}
async function sources(directory){const result=[];for(const entry of await fs.readdir(directory,{withFileTypes:true})){const file=path.join(directory,entry.name);if(entry.isDirectory())result.push(...await sources(file));else if(/\.(?:js|mjs)$/.test(entry.name))result.push(file);}return result;}
try {
  await build({configFile:path.join(root,'vite.config.ts'),build:{outDir:stage,emptyOutDir:false},logLevel:'warn'});
  const files=await sources(stage);
  for(const file of files){
    const body=await fs.readFile(file,'utf8');
    const source=ts.createSourceFile(file,body,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS);
    const inspect=(node)=>{
      let specifier;
      if((ts.isImportDeclaration(node)||ts.isExportDeclaration(node))&&node.moduleSpecifier&&ts.isStringLiteral(node.moduleSpecifier))specifier=node.moduleSpecifier.text;
      if(ts.isCallExpression(node)&&node.expression.kind===ts.SyntaxKind.ImportKeyword&&node.arguments[0]&&ts.isStringLiteral(node.arguments[0]))specifier=node.arguments[0].text;
      if(specifier&&!/^(?:\.{1,2}\/|\/|data:)/.test(specifier))throw new Error(`Unbundled browser import ${specifier} in ${path.relative(stage,file)}`);
      ts.forEachChild(node,inspect);
    };
    ts.forEachChild(source,inspect);
  }
  const manifest=JSON.parse(await fs.readFile(path.join(stage,'source-modules.json'),'utf8'));
  const index=await fs.readFile(path.join(stage,'index.html'));
  await fs.writeFile(path.join(stage,'.mirally-build.json'),JSON.stringify({commit:manifest.commit,modules:manifest.modules.length,browserModules:files.length,indexSha256:createHash('sha256').update(index).digest('hex')},null,2)+'\n');
  let hadPrevious=false;
  try{await fs.rename(target,retired);hadPrevious=true;}catch(error){if(error.code!=='ENOENT')throw error;}
  try{await fs.rename(stage,target);}catch(error){if(hadPrevious)await fs.rename(retired,target);throw error;}
  if(hadPrevious)await fs.rm(owned(retired),{recursive:true,force:true});
  console.log(`Original Mirally Control UI built: ${files.length} browser modules, no unbundled imports`);
} finally {
  await fs.rm(owned(stage),{recursive:true,force:true});
}
