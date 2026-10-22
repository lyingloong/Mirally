// Read-only reuse of the pinned official config and Gateway CLI APIs.
import {readFileSync, readdirSync} from 'node:fs';
import {createInterface} from 'node:readline';
import {pathToFileURL} from 'node:url';

export async function nativeDependencies(packageRoot = new URL('./node_modules/openclaw/', import.meta.url)) {
  if (JSON.parse(readFileSync(new URL('package.json', packageRoot), 'utf8')).version !== '2026.9.6') throw Error('Unsupported native version');
  const dist = new URL('dist/', packageRoot);
  const entries = readdirSync(dist).filter(name => /^config-cli-[\w-]{8}\.mjs$/.test(name));
  if (entries.length !== 1) throw Error('Native config API unavailable');
  const source = readFileSync(new URL(entries[0], dist), 'utf8');
  const dependency = name => {
    // Use exactly the same public-name facades as the pinned config.get CLI.
    const match = source.match(new RegExp(`const \\{ ${name} \\} = await import\\("(\\./[^"/]+\\.mjs)"\\)`));
    if (!match) throw Error('Native config API unavailable');
    return import(new URL(match[1], dist));
  };
  const [config, schema, redact] = await Promise.all([
    import(new URL('config/config.js', dist)),
    dependency('buildRuntimeConfigSchemaFromRegistry'), dependency('redactConfigObject'),
  ]);
  let rpc;
  return {
    read: () => config.readConfigFileSnapshotWithPluginMetadata({observe:false}),
    clear: () => config.clearConfigCache(),
    schema: schema.buildRuntimeConfigSchemaFromRegistry,
    redact: redact.redactConfigObject,
    call: async (method, config) => {
      rpc ??= import(new URL('gateway-rpc.runtime.js', dist));
      return (await rpc).callGatewayFromCliRuntime(method, {config,timeout:'3000',json:true}, {}, {progress:false});
    },
  };
}

export function createSettingsReader(native) {
  return async action => {
    if (action === 'profiles') {
      native.clear();
      const {snapshot, pluginMetadataSnapshot} = await native.read();
      if (snapshot?.valid !== true || !pluginMetadataSnapshot) throw Error('Native config invalid');
      const {uiHints} = native.schema(pluginMetadataSnapshot.manifestRegistry, snapshot.sourceConfig);
      const config = native.redact(snapshot.config, uiHints);
      const servers = config.mcp?.servers ?? {};
      const plugin = config.plugins?.entries?.['mirally-memory']?.config ?? {};
      const defaults = config.agents?.defaults ?? {};
      if ([servers, plugin, defaults].some(value => !value || typeof value !== 'object' || Array.isArray(value))) throw Error('Native config invalid');
      return {servers,plugin,defaults};
    }
    if (action === 'memory-status') {
      native.clear();
      const {snapshot, pluginMetadataSnapshot} = await native.read();
      if (snapshot?.valid !== true || !pluginMetadataSnapshot) throw Error('Native config invalid');
      return native.call('mirally.memory.status', snapshot.config);
    }
    throw Error('Unsupported settings read');
  };
}

export async function serve(input = process.stdin, output = process.stdout, dependencies = nativeDependencies) {
  // Native diagnostic output never belongs in the private JSONL protocol.
  console.log = (...args) => console.error(...args);
  let reader;
  for await (const line of createInterface({input, crlfDelay:Infinity})) {
    let request, reply;
    try {
      if (line.length > 1024) throw Error();
      request = JSON.parse(line);
      if (!['profiles','memory-status'].includes(request.action) || typeof request.id !== 'number') throw Error();
      reader ??= createSettingsReader(await dependencies());
      reply = {id:request.id,ok:true,result:await reader(request.action)};
    } catch { reply = {id:request?.id ?? null,ok:false,error:'Native settings read unavailable'}; }
    await new Promise(resolve => output.write(JSON.stringify(reply) + '\n', resolve));
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { await serve(); } catch { process.exitCode = 1; }
}
