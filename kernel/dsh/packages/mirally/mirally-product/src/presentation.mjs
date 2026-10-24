/** Serve the existing Mirally skin without an OpenClaw Host or code injection bridge. */
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
const css = ['mirally', 'companion', 'work', 'subagents', 'activity', 'memory', 'models'];
const scripts = ['mirally', 'office', 'intake', 'context', 'work', 'files', 'companion', 'subagents', 'activity', 'memory'];
export async function originalPresentation(root, origin) {
  const assets = join(root, 'mirally/assets');
  const styles = await Promise.all(css.map(name => readFile(join(assets, `control-ui-${name}.css`), 'utf8')));
  const code = await Promise.all(scripts.map(name => readFile(join(assets, `control-ui-${name}.js`), 'utf8')));
  const pets = Object.fromEntries(await Promise.all([['otter', 'mirally-pet-otter.svg'], ['fox', 'mirally-pet.svg']].map(async ([key, name]) => [key, await readFile(join(assets, name), 'utf8')])));
  const replacements = {__MIRALLY_ORIGIN__: origin, __MIRALLY_STYLE__: styles.join('\n'), __MIRALLY_MARK__: '/experience/mirally-128.png', __MIRALLY_PETS__: pets, __MIRALLY_VERSION__: '2026.10.23.original-ui', __MIRALLY_SUBAGENT_IDS__: []};
  let result = code.join('\n'); for (const [key, value] of Object.entries(replacements)) result = result.replaceAll(key, JSON.stringify(value));
  return result;
}
