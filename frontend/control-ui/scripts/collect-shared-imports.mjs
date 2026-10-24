// Maintenance helper. Resolve runtime imports only; never copy backend type-only graphs.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const tree=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const blobs=new Set(tree.tree.filter(item=>item.type==='blob').map(item=>item.path));
const needed=new Set();
const visited=new Set();
const queue=[];
function walk(directory) {for(const item of fs.readdirSync(directory,{withFileTypes:true})){const file=path.join(directory,item.name);if(item.isDirectory())walk(file);else if(/\.(?:ts|js|mjs)$/.test(item.name)&&!item.name.endsWith('.d.ts'))queue.push(file);}}
walk(path.join(root,'ui/src'));
function resolveSource(relative) {
  const normalized=path.posix.normalize(relative.replaceAll('\\','/'));
  for(const candidate of [normalized,normalized.replace(/\.[cm]?js$/,'.ts'),normalized+'.ts',normalized+'/index.ts']) {
    if(blobs.has(candidate)) return candidate;
  }
  return null;
}
function accept(specifier,file) {
  if(specifier === '../logging/redact.js' && /\/src\/agents\/tool-display(?:-common|-exec)?\.ts$/.test(file.replaceAll('\\','/')))return;
  let candidate;
  if(specifier.startsWith('.')) candidate=resolveSource(path.posix.join(path.relative(root,path.dirname(file)).replaceAll('\\','/'),specifier));
  else {
    const match=/^@openclaw\/([^/]+)(?:\/(.+))?$/.exec(specifier);
    if(match&&!['uirouter','libterminal'].includes(match[1]))candidate=resolveSource(`packages/${match[1]}/src/${match[2]??(match[1]==='mermaid-renderer'?'renderer':'index')}.ts`);
  }
  if(!candidate || candidate.startsWith('ui/'))return;
  const absolute=path.join(root,candidate);
  if(fs.existsSync(absolute)) {if(!visited.has(absolute))queue.push(absolute);}
  else needed.add(candidate);
}
while(queue.length) {
  const file=queue.pop();if(visited.has(file))continue;visited.add(file);
  if(!fs.existsSync(file))continue;
  const source=ts.createSourceFile(file,fs.readFileSync(file,'utf8'),ts.ScriptTarget.Latest,true);
  for(const statement of source.statements) {
    if(ts.isImportDeclaration(statement)&&ts.isStringLiteral(statement.moduleSpecifier)) {
      const clause=statement.importClause;
      if(clause?.isTypeOnly)continue;
      if(clause?.namedBindings&&ts.isNamedImports(clause.namedBindings)&&!clause.name&&clause.namedBindings.elements.every(binding=>binding.isTypeOnly))continue;
      accept(statement.moduleSpecifier.text,file);
    }
    if(ts.isExportDeclaration(statement)&&!statement.isTypeOnly&&statement.moduleSpecifier&&ts.isStringLiteral(statement.moduleSpecifier))accept(statement.moduleSpecifier.text,file);
  }
  function dynamic(node){if(ts.isCallExpression(node)&&node.expression.kind===ts.SyntaxKind.ImportKeyword&&ts.isStringLiteral(node.arguments[0]))accept(node.arguments[0].text,file);ts.forEachChild(node,dynamic);}
  ts.forEachChild(source,dynamic);
}
fs.writeFileSync(process.argv[3],JSON.stringify([...needed].sort(),null,2)+'\n');
console.log(JSON.stringify({scanned:visited.size,missing:needed.size,paths:[...needed].sort()}));
