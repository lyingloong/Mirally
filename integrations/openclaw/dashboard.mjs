// Thin adapter to the unchanged dashboard command in pinned OpenClaw 2026.9.6.
// Its JSON stdout contains a one-time authentication URL: capture it privately.
import {readFileSync, readdirSync} from 'node:fs';
import {pathToFileURL} from 'node:url';

const pinnedVersion = '2026.9.6';
const defaultPackage = new URL('./node_modules/openclaw/', import.meta.url);

export async function runDashboard(packageRoot = defaultPackage, write = value =>
  new Promise(resolve => process.stdout.write(JSON.stringify(value) + '\n', resolve))) {
  let entry;
  try {
    if (JSON.parse(readFileSync(new URL('package.json', packageRoot), 'utf8')).version !== pinnedVersion) return 78;
    const dist = new URL('dist/', packageRoot);
    const entries = readdirSync(dist).filter(name => /^dashboard-[A-Za-z0-9_-]{8}\.mjs$/.test(name));
    if (entries.length !== 1) return 78;
    entry = new URL(entries[0], dist);
  } catch { return 78; }

  let result, exitCode;
  try {
    const {dashboardCommand} = await import(entry);
    if (typeof dashboardCommand !== 'function') return 78;
    await dashboardCommand({
      log: value => { result = JSON.parse(value); },
      error: () => {},
      exit: code => { exitCode = code; },
    }, {json: true});
  } catch {
    result = {ok: false, reason: 'Native dashboard temporarily unavailable.'};
    exitCode = 1;
  }
  if (!result || typeof result !== 'object') {
    result = {ok: false, reason: 'Native dashboard temporarily unavailable.'};
  }
  await write(result);
  return exitCode ?? (result.ok === true ? 0 : 1);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exit(await runDashboard());
}
