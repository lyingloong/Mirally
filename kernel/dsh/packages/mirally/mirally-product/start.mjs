/** Launch the Mirally-owned composition from the repository's built kernel. */
import {parseArgs} from 'node:util';
import {mkdirSync, writeFileSync} from 'node:fs';
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
  MIRALLY_PYTHON: resolve(values.python), MIRALLY_UI_DIRECTORY: join(root, 'frontend/control-ui/dist'),
  MIRALLY_PORT: String(port), MIRALLY_TEST_MODE: values['test-mode'] ? '1' : '0',
  MIRALLY_WORKSPACE: join(home, 'workspace'),
  DSH_TELEMETRY_DISABLED: '1'});
const {boot, installFailLoud} = await import('@deepseek-ai/dsh-app-boot');
const {loadMirallyComposition} = await import('./composition.mjs');
const {patches} = loadMirallyComposition();
const directory = join(home, 'host');
mkdirSync(directory, {recursive: true});
const path = join(directory, 'cordis.yml');
writeFileSync(path, '[]\n');
installFailLoud('mirally');
const ctx = await boot('mirally', path, patches);
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
