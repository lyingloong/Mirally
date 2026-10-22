/** Validate user-selected integrations before loading code or opening a transport. */
import {realpath, stat, readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {isAbsolute, resolve, dirname, relative, sep, extname} from 'node:path';

const ID = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$/;
const VERSION = /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/;

/** Throw when an untrusted JSON configuration field has the wrong type. */
export function assertRecord(value, label) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label} must be an object`);
  return value;
}

/** Check canonical containment, including an exact directory match. */
export function inside(path, directory) {
  const result = relative(directory, path);
  return result === '' || (!isAbsolute(result) && result !== '..' && !result.startsWith(`..${sep}`));
}

/** Resolve a configured path relative to the portable repository root. */
export function resolvePath(path, root) {
  if (typeof path !== 'string' || !path.trim() || path.includes('\0')) throw new Error('A nonempty filesystem path is required');
  return resolve(root, path);
}

function array(value, label, max = 100) {
  if (!Array.isArray(value) || value.length > max) throw new Error(`${label} must be an array of at most ${max} items`);
  return value;
}

function strings(value, label, max = 100) {
  return array(value, label, max).map(item => {
    if (typeof item !== 'string' || item.includes('\0') || item.length > 8192) throw new Error(`${label} must contain strings`);
    return item;
  });
}

function dictionary(value, label) {
  const record = assertRecord(value ?? {}, label);
  for (const [key, entry] of Object.entries(record)) {
    if (typeof entry !== 'string' || key.includes('\0') || entry.includes('\0')) throw new Error(`${label} must contain string values`);
  }
  return {...record};
}

function integer(value, fallback, minimum, maximum, label) {
  const result = value ?? fallback;
  if (!Number.isInteger(result) || result < minimum || result > maximum) throw new Error(`${label} must be between ${minimum} and ${maximum}`);
  return result;
}

/** Normalize one trusted-user integration; this never installs packages. */
export async function normalizeExtension(input, options) {
  const value = assertRecord(input, 'extension');
  if (!ID.test(value.id ?? '')) throw new Error('Extension id must use 1–64 letters, digits, dots, underscores or hyphens');
  if (value.enabled !== undefined && typeof value.enabled !== 'boolean') throw new Error('enabled must be boolean');
  const version = value.version ?? '0.1.0';
  if (!VERSION.test(version)) throw new Error('Extension version must be a semantic version');
  const type = value.type ?? value.kind ?? 'bundle';
  if (!['bundle', 'skill', 'mcp', 'plugin'].includes(type)) throw new Error(`Unsupported extension type: ${type}`);
  const config = assertRecord(value.config ?? {}, 'extension.config');
  if ((config.apiVersion ?? 1) !== 1) throw new Error('Unsupported Mirally extension API version; this runtime supports version 1');
  if (value.enabled === false) return {id: value.id, type, version, enabled: false, dependsOn: [], skillDirs: [],
    scripts: new Map(), mcp: [], stored: JSON.parse(JSON.stringify({...value, type, version, config}))};
  const skills = type === 'skill' ? [value.path] : config.skills ?? config.skillDirs ?? [];
  const skillDirs = [];
  for (const path of strings(skills, 'skills')) {
    const canonical = await realpath(resolvePath(path, options.root));
    if (!(await stat(canonical)).isDirectory()) throw new Error('Skill roots must be directories');
    if (!skillDirs.includes(canonical)) skillDirs.push(canonical);
  }
  const scriptConfigs = array(config.scripts ?? [], 'scripts');
  const scripts = new Map();
  for (const entry of scriptConfigs) {
    const script = assertRecord(entry, 'script');
    const id = script.id;
    if (!ID.test(id ?? '') || scripts.has(id)) throw new Error('Script ids must be valid and unique within one extension');
    const path = await realpath(resolvePath(script.path, options.root));
    if (!skillDirs.some(directory => inside(path, directory))) throw new Error('Every script must be inside an explicitly configured Skill directory');
    if (!(await stat(path)).isFile()) throw new Error('Script must name a regular file');
    const interpreter = script.interpreter ?? (extname(path) === '.py' ? 'python' : undefined);
    if (!['python', 'node'].includes(interpreter)) throw new Error('Script interpreter must be explicitly python or node');
    if (interpreter === 'python' && extname(path).toLowerCase() !== '.py') throw new Error('Python scripts require a .py file');
    if (interpreter === 'node' && !['.js', '.mjs', '.cjs'].includes(extname(path).toLowerCase())) throw new Error('Node scripts require a .js, .mjs or .cjs file');
    const workingDirectory = await realpath(resolvePath(script.workingDirectory ?? dirname(path), options.root));
    if (!(await stat(workingDirectory)).isDirectory()) throw new Error('Script working directory must exist');
    const sha256 = createHash('sha256').update(await readFile(path)).digest('hex');
    if (script.sha256 && script.sha256 !== sha256) throw new Error(`Script changed since configuration: ${id}; review the file and reconfigure its fingerprint`);
    script.sha256 = sha256;
    scripts.set(id, {
      id, path, configuredPath: script.path, interpreter, workingDirectory,
      sha256,
      timeoutMs: integer(script.timeoutMs, 30_000, 100, 300_000, 'timeoutMs'),
      maxOutputBytes: integer(script.maxOutputBytes, 65_536, 256, 1_048_576, 'maxOutputBytes'),
      env: dictionary(script.env, 'script.env'),
    });
  }
  const mcpConfigs = type === 'mcp' ? [config] : config.mcp ?? config.mcpServers ?? [];
  const mcp = [];
  const names = new Set();
  for (const raw of array(mcpConfigs, 'mcp')) {
    const server = assertRecord(raw, 'MCP server');
    if (!/^[A-Za-z0-9_-]{1,32}$/.test(server.serverName ?? '') || names.has(server.serverName)) throw new Error('MCP serverName must be valid and unique');
    names.add(server.serverName);
    const transport = server.transport ?? 'stdio';
    const common = {
      serverName: server.serverName, transport,
      toolCallTimeoutMs: integer(server.toolCallTimeoutMs, 60_000, 100, 300_000, 'toolCallTimeoutMs'),
      failOnStartupError: true,
      readOnly: server.readOnly === true,
      maxInstructionBytes: integer(server.maxInstructionBytes, 32_768, 256, 1_048_576, 'maxInstructionBytes'),
      reconnect: server.reconnect ?? {enabled: true},
    };
    if (transport === 'stdio') {
      if (typeof server.command !== 'string' || !server.command.trim() || server.command.includes('\0')) throw new Error('MCP stdio requires a command');
      const command = server.command === 'python' ? options.python : server.command === 'node' ? process.execPath
        : server.command.startsWith('.') || isAbsolute(server.command) ? resolvePath(server.command, options.root) : server.command;
      mcp.push({...common, command, args: strings(server.args ?? [], 'MCP args'), env: dictionary(server.env, 'MCP env'),
        cwd: resolvePath(server.cwd ?? options.root, options.root)});
    } else if (transport === 'streamable-http') {
      const url = new URL(server.url);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('MCP requires an HTTP(S) URL without embedded credentials');
      mcp.push({...common, url: url.href, headers: dictionary(server.headers, 'MCP headers')});
    } else throw new Error(`Unsupported MCP transport: ${transport}`);
  }
  const module = type === 'plugin' ? value.path : config.module;
  if (module !== undefined && (typeof module !== 'string' || !module.trim() || module.includes('\0'))) throw new Error('Plugin module must be a package or file path');
  const dependsOn = strings(config.dependsOn ?? [], 'dependsOn');
  if (dependsOn.includes(value.id)) throw new Error('An extension cannot depend on itself');
  return {
    id: value.id, type, version, enabled: value.enabled !== false, module,
    pluginConfig: config.pluginConfig ?? (type === 'plugin' ? config : {}),
    dependsOn, skillDirs, scripts, mcp,
    stored: JSON.parse(JSON.stringify({...value, id: value.id, type, version, enabled: value.enabled !== false, config})),
  };
}

/** Remove connection credentials from UI responses while preserving configured presence. */
export function redactExtension(value) {
  if (Array.isArray(value)) return value.map(redactExtension);
  if (value === null || typeof value !== 'object') return value;
  const result = {};
  for (const [key, entry] of Object.entries(value)) {
    if (['env', 'headers'].includes(key)) result[key] = Object.fromEntries(Object.keys(entry ?? {}).map(name => [name, '••••']));
    else if (/api.?key|password|secret|token|authorization/i.test(key)) result[key] = entry ? '••••' : '';
    else result[key] = redactExtension(entry);
  }
  return result;
}
