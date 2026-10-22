/** Launch the Mirally-owned composition from the repository's built kernel. */
import {parseArgs} from 'node:util';
import {mkdirSync, readFileSync, writeFileSync} from 'node:fs';
import {resolve, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const {values} = parseArgs({options: {root: {type: 'string'}, home: {type: 'string'}, python: {type: 'string'}, port: {type: 'string', default: '0'}, 'test-mode': {type: 'boolean', default: false}}});
const root = resolve(values.root ?? fileURLToPath(new URL('../../../../../', import.meta.url)));
if (!values.home || !values.python) throw new Error('Mirally requires explicit --home and --python');
const home = resolve(values.home);
mkdirSync(join(home, 'workspace'), {recursive: true});
const port = Number(values.port);
if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('Invalid Mirally port');
Object.assign(process.env, {DSH_HOME: home, MIRALLY_ROOT: root, MIRALLY_HOME: home,
  MIRALLY_PYTHON: resolve(values.python), MIRALLY_UI_DIRECTORY: join(root, 'mirally/framework_ui'),
  MIRALLY_PORT: String(port), MIRALLY_TEST_MODE: values['test-mode'] ? '1' : '0',
  MIRALLY_WORKSPACE: join(home, 'workspace'),
  DSH_TELEMETRY_DISABLED: '1'});
const {boot, installFailLoud} = await import('@deepseek-ai/dsh-app-boot');
const patch = readFileSync(new URL('./cordis.patch.yml', import.meta.url), 'utf8');
// Leave the Loader's !!js configuration expressions intact while resolving
// module names against this workspace, rather than the user's data directory.
const lines = patch.split('\n');
const config = [];
let inserting = false;
for (const line of lines) {
  if (line.startsWith('- insert:')) { inserting = true; continue; }
  if (!inserting) continue;
  let entry = line.startsWith('    ') ? line.slice(4) : line;
  const match = /^(\s*name: )'([^']+)'\s*$/.exec(entry);
  if (match) entry = match[1] + JSON.stringify(import.meta.resolve(match[2]));
  config.push(entry);
}
if (!config.some(line => line.includes('mirally-product'))) throw new Error('Mirally composition is missing');
const directory = join(home, 'host');
mkdirSync(directory, {recursive: true});
const path = join(directory, 'cordis.yml');
writeFileSync(path, config.join('\n'));
installFailLoud('mirally');
const ctx = await boot('mirally', path);
const product = ctx.get('mirallyProduct');
if (!product?.ready) { await ctx.fiber.dispose(); throw new Error('Mirally product did not become ready'); }
let closing;
function stop() { closing ??= Promise.resolve(ctx.fiber.dispose()).then(() => { process.stdin.destroy(); process.exitCode = 0; }); return closing; }
ctx.on('mirally/shutdown-requested', () => void stop());
process.once('SIGINT', () => void stop());
process.once('SIGTERM', () => void stop());
// A supervising Python launcher closes stdin on normal exit or unexpected death.
process.stdin.resume();
process.stdin.once('end', () => void stop());
