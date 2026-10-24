// Filename-only browser adapter for the upstream media presentation helpers.
// No host filesystem or general path resolution is exposed.
function basename(value: string, windows = true): string {
  const name=value.replace(windows ? /[/\\]+$/ : /\/+$/, '').split(windows ? /[/\\]/ : /\//).at(-1) ?? '';
  return windows ? name.replace(/^[a-zA-Z]:/, '') : name;
}
function extname(value: string, windows = true): string {
  const name=basename(value,windows);
  if(name === '.' || name === '..') return '';
  const index=name.lastIndexOf('.');
  return index > 0 ? name.slice(index) : '';
}
function parse(value: string) {
  const base=basename(value);
  const ext=extname(base);
  return {root:'',dir:'',base,ext,name:ext ? base.slice(0,-ext.length) : base};
}
const posix={basename:(value:string)=>basename(value,false),extname:(value:string)=>extname(value,false)};
export default {basename,extname,parse,posix,win32:{basename,extname,parse}};
