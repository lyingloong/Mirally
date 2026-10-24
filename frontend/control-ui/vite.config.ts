import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { defineConfig } from 'vite';

const root = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const packages = new Set(['gateway-client','gateway-protocol','markdown-core','media-core','mermaid-renderer','model-catalog-core','net-policy','normalization-core','retry','session-url-contract','terminal-core','workboard-contract','tool-call-repair','acp-core']);
const upstream = JSON.parse(fs.readFileSync(path.join(root,'upstream-files.json'),'utf8'));

export default defineConfig({
  root: path.join(root,'ui'),
  base: '/',
  publicDir: path.join(root,'ui/public'),
  cacheDir: path.join(root,'node_modules/.vite'),
  define: {
    'globalThis.OPENCLAW_CONTROL_UI_BUILD_INFO': JSON.stringify({version:'2026.9.6',commit:upstream.commit,buildId:'mirally-control-ui-'+upstream.commit.slice(0,12),builtAt:null}),
  },
  resolve: {alias:[{find:'json5',replacement:require.resolve('json5/dist/index.mjs')}]},
  build: {outDir:path.join(root,'dist'),emptyOutDir:true,sourcemap:false,chunkSizeWarningLimit:1600},
  plugins: [
    {
      name:'mirally-browser-shared-sources',
      enforce:'pre',
      resolveId(source, importer) {
        if(source === 'node:path' && importer && /\/packages\/media-core\/src\/(?:mime|file-name)\.ts$/.test(importer.replaceAll('\\','/'))) return path.join(root,'ui/src/lib/mirally-browser-path.ts');
        if(source.startsWith('node:')) throw new Error('Node runtime import is not allowed in the browser: '+source+' from '+importer);
        if (source === '../logging/redact.js' && importer && /\/src\/agents\/tool-display(?:-common|-exec)?\.ts$/.test(importer.replaceAll('\\','/'))) {
          return path.join(root,'ui/src/lib/browser-redact.ts');
        }
        const match = /^@openclaw\/([^/]+)(?:\/(.+))?$/.exec(source);
        if (!match || !packages.has(match[1])) return null;
        const entry = match[2] ?? (match[1] === 'mermaid-renderer' ? 'renderer' : 'index');
        return path.join(root,'packages',match[1],'src',entry+'.ts');
      },
    },
    {
      name:'mirally-original-locales',
      resolveId(id) {return id.startsWith('virtual:openclaw-control-ui-locale/') ? '\0'+id : null;},
      load(id) {
        const prefix='\0virtual:openclaw-control-ui-locale/';
        if (!id.startsWith(prefix)) return null;
        const locale=id.slice(prefix.length);
        if (!/^[A-Za-z-]+$/.test(locale)) throw new Error('Invalid locale');
        const catalog=fs.readFileSync(path.join(root,'locales',locale+'.json'),'utf8');
        return 'export default '+catalog+';';
      },
    },
    {
      name:'mirally-browser-boundary',
      generateBundle() {
        const modules=Array.from(this.getModuleIds()).filter(id => !id.includes('node_modules') && !id.startsWith('\0')).map(id=>path.relative(root,id).replaceAll('\\','/')).filter(id=>!id.startsWith('..')).sort();
        const forbidden=modules.filter(id=>/^(?:src\/(?:gateway\/(?:server|server-methods)|agents\/(?:agent-scope|pi-embedded)|auto-reply\/reply\/get-reply))/.test(id));
        if (forbidden.length) throw new Error('Backend runtime leaked into UI: '+forbidden.join(', '));
        this.emitFile({type:'asset',fileName:'source-modules.json',source:JSON.stringify({commit:upstream.commit,modules},null,2)+'\n'});
      },
    },
  ],
});
