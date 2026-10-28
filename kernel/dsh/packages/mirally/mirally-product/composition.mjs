/** Mirally inherits the shipped DSH base; this product layer only states differences.
 * Keep the Loader's tagged expressions intact and use its real patch parser and
 * composer. Do not rebuild a second list of selected DSH capabilities here.
 */
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {loadOverlayPatches, composeEntries} from '@deepseek-ai/dsh-app-boot';

const productRequire = createRequire(new URL('./package.json', import.meta.url));
export const BASE_PATCH = productRequire.resolve('@deepseek-ai/dsh-base/cordis.patch.yml');
export const PRODUCT_PATCH = fileURLToPath(new URL('./cordis.patch.yml', import.meta.url));
const baseRequire = createRequire(productRequire.resolve('@deepseek-ai/dsh-base/package.json'));

function resolveNames(patches, require) {
  const visit = entry => {
    if (typeof entry.name === 'string' && !entry.name.startsWith('cordis:') && !entry.name.startsWith('file:')) {
      entry.name = pathToFileURL(require.resolve(entry.name)).href;
    }
    if (entry.group && Array.isArray(entry.config)) entry.config.forEach(visit);
  };
  for (const patch of patches) {
    patch.insert?.forEach(visit);
    if (patch.name) visit(patch);
  }
  return patches;
}

/** Return native patch layers for boot, plus the same composed rows for diagnostics. */
export function loadMirallyComposition() {
  const base = resolveNames(loadOverlayPatches('mirally', BASE_PATCH), baseRequire);
  const product = resolveNames(loadOverlayPatches('mirally', PRODUCT_PATCH), productRequire);
  const patches = [...base, ...product];
  const entries = composeEntries([patches]);
  if (!entries.some(entry => entry.id === 'mirally-product')) throw new Error('Mirally product overlay is missing');
  return {patches, entries};
}
