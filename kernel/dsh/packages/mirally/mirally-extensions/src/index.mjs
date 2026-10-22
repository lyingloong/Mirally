/** Mirally's native Skill, MCP and trusted-plugin lifecycle, with controlled Skill scripts. */
import {pathToFileURL} from 'node:url';
import {readFile, realpath} from 'node:fs/promises';
import {resolve, dirname, isAbsolute} from 'node:path';
import {createHash, randomUUID} from 'node:crypto';
import {defineTool} from '@deepseek-ai/dsh-tools';
import {FileSystemSkillProvider} from '@deepseek-ai/dsh-skill-filesystem';
import {createMcpConnection} from '@deepseek-ai/dsh-mcp-client';
import {normalizeExtension, redactExtension, resolvePath, inside, assertRecord} from './configuration.mjs';
import {runConfiguredScript} from './script.mjs';

export const name = 'mirally-extensions';
export const inject = ['mirallyProduct', 'tools', 'skills'];

const jsonOutput = {schema: {type: 'json'}, render: (_args, value) => [{type: 'text', text: JSON.stringify(value)}]};

function combine(...signals) {
  const active = signals.filter(Boolean);
  return active.length ? AbortSignal.any(active) : new AbortController().signal;
}

function copySecrets(incoming, previous) {
  if (incoming === '••••' || incoming === '********' || incoming === '<configured>') {
    if (previous === undefined) throw new Error('A masked credential has no saved value; enter its actual value');
    return previous;
  }
  if (Array.isArray(incoming)) return incoming.map((value, index) => copySecrets(value, previous?.[index]));
  if (incoming === null || typeof incoming !== 'object') return incoming;
  return Object.fromEntries(Object.entries(incoming).map(([key, value]) => [key, copySecrets(value, previous?.[key])]));
}

function safeError(error, spec) {
  let text = error instanceof Error ? error.message : String(error);
  for (const server of spec?.mcp ?? []) {
    for (const value of [...Object.values(server.env ?? {}), ...Object.values(server.headers ?? {})]) {
      if (value.length > 3) text = text.split(value).join('<redacted>');
    }
  }
  return text.slice(0, 2048);
}

function execution(input, scope = {}) {
  return {
    agent: scope.agent,
    signal: combine(scope.signal, input.signal),
    sessionId: input.session_id ?? scope.session_id ?? scope.agent?.session?.id ?? scope.agent?.id,
  };
}

/**
 * Create the integration service in one product composition.
 * The caller owns the Cordis context; disposal removes native registrations and connections.
 */
export async function createExtensions(ctx, options) {
  const runtime = new ExtensionRuntime(ctx, options);
  ctx.effect(() => () => runtime.dispose(), 'mirally.extensions');
  await runtime.initialize();
  return runtime;
}

/** Mount the service using the ordinary DSH plugin loader. */
export async function apply(ctx, options) {
  const runtime = await createExtensions(ctx, options);
  ctx.provide('mirallyExtensions', runtime);
}

class ExtensionRuntime {
  constructor(ctx, options) {
    this.ctx = ctx;
    this.product = ctx.mirallyProduct;
    this.root = resolve(options.root);
    this.home = resolve(options.home);
    this.python = options.python ?? resolve(this.root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
    this.initial = options.extensions ?? [];
    this.entries = new Map();
    this.serverOwners = new Map();
    this.closed = false;
    this.queue = Promise.resolve();
    this.stopController = new AbortController();
  }

  async initialize() {
    this.registerTools();
    const settings = await this.product.rpc('internal.settings.get', {});
    const specs = settings.extensions?.length ? settings.extensions : this.initial;
    if (!Array.isArray(specs)) throw new Error('Saved extensions must be an array');
    const remaining = new Map();
    for (const input of specs) {
      try {
        const spec = await normalizeExtension(input, this);
        if (remaining.has(spec.id)) throw new Error(`Duplicate extension id: ${spec.id}`);
        remaining.set(spec.id, spec);
      } catch (error) {
        const spec = {id: input.id, stored: input, skillDirs: [], scripts: new Map(), mcp: []};
        this.entries.set(input.id, {spec, status: 'error', error: safeError(error, spec),
          controller: new AbortController(), connections: new Map(), activeScripts: new Set()});
      }
    }
    while (remaining.size) {
      const candidate = [...remaining.values()].find(spec => !spec.enabled || spec.dependsOn.every(id => this.entries.get(id)?.status === 'active'));
      if (!candidate) {
        for (const spec of remaining.values()) this.entries.set(spec.id, {spec, status: 'error', error: 'Missing, inactive or cyclic extension dependency'});
        break;
      }
      remaining.delete(candidate.id);
      await this.activate(candidate);
    }
  }

  serial(operation) {
    const result = this.queue.catch(() => {}).then(() => {
      if (this.closed) throw new Error('Extension runtime is closed');
      return operation();
    });
    this.queue = result;
    return result;
  }

  assertActive(entry) {
    if (this.closed || entry.status !== 'active' || entry.controller.signal.aborted || this.entries.get(entry.spec.id) !== entry) {
      throw new Error('This extension is disabled or has been replaced');
    }
  }

  async authorize(entry, sessionId, effect, confirmed = false) {
    this.assertActive(entry);
    if (!sessionId && effect === 'query') {
      const result = await this.product.rpc('guard.check', {effect: 'query'});
      if (!result.allow) throw new Error(result.reasons.join('; '));
      return;
    }
    if (sessionId === undefined && effect === 'query') return;
    if (typeof sessionId !== 'string' || !sessionId) throw new Error('This operation requires an active conversation');
    const result = await this.product.guard(sessionId, effect, confirmed === true);
    if (result === false || result?.allow === false) throw new Error('Product authorization refused this operation');
    this.assertActive(entry);
  }

  async activate(spec) {
    const entry = {spec, status: spec.enabled ? 'starting' : 'disabled', controller: new AbortController(), connections: new Map(), activeScripts: new Set()};
    this.entries.set(spec.id, entry);
    if (!spec.enabled) return entry;
    try {
      for (const dependency of spec.dependsOn) {
        if (this.entries.get(dependency)?.status !== 'active') throw new Error(`Dependency is not active: ${dependency}`);
      }
      for (const server of spec.mcp) {
        if (this.serverOwners.has(server.serverName)) throw new Error(`MCP namespace is already in use: ${server.serverName}`);
      }
      const knownSkills = new Set((await this.ctx.skills.list({cwd: this.root})).map(skill => skill.name));
      const preflight = new FileSystemSkillProvider(this.ctx, {signal: entry.controller.signal, invalidate() {}}, {
        providerName: `mirally-${spec.id}`, includeDefaultRoots: false, customSkillDirs: spec.skillDirs, watch: false,
      });
      try {
        const observation = await preflight.list({cwd: this.root, signal: entry.controller.signal});
        const candidates = Array.isArray(observation) ? observation : observation.candidates;
        const ownNames = new Set();
        for (const skill of candidates) {
          if (knownSkills.has(skill.name) || ownNames.has(skill.name)) throw new Error(`Skill name conflicts with an enabled integration: ${skill.name}`);
          ownNames.add(skill.name);
        }
      } finally { await preflight.dispose(); }
      const plugin = {name: `mirally-extension-${spec.id}`, apply: async inner => {
        if (spec.skillDirs.length) {
          let provider;
          inner.skills.registerProvider(control => {
            provider = new FileSystemSkillProvider(inner, control, {
              providerName: `mirally-${spec.id}`, includeDefaultRoots: false, customSkillDirs: spec.skillDirs, watch: false,
            });
            return provider;
          });
          inner.effect(() => () => provider.dispose(), 'mirally.skill-provider');
        }
        for (const server of spec.mcp) {
          this.serverOwners.set(server.serverName, entry);
          inner.effect(() => () => { if (this.serverOwners.get(server.serverName) === entry) this.serverOwners.delete(server.serverName); }, 'mirally.mcp-namespace');
          const connection = createMcpConnection(inner, server, {beforeToolCall: async exec => {
            const call = execution({}, exec);
            await this.authorize(entry, call.sessionId, server.readOnly ? 'query' : 'script',
              this.product.authorizations.getStore()?.confirmed === true);
            call.signal.throwIfAborted();
          }});
          entry.connections.set(server.serverName, connection);
          const outcome = await connection.ready;
          if (outcome.error) throw new Error(`MCP server failed to connect: ${server.serverName}`, {cause: outcome.error});
        }
        if (spec.module !== undefined) {
          const isPath = isAbsolute(spec.module) || spec.module.startsWith('.');
          const module = await import(isPath ? pathToFileURL(resolvePath(spec.module, this.root)).href : spec.module);
          const descriptor = module.mirally;
          if (descriptor?.apiVersion !== undefined && descriptor.apiVersion !== 1) throw new Error('Plugin declares an incompatible Mirally API version');
          const native = module.default ?? module;
          await inner.plugin(native, spec.pluginConfig);
        }
      }};
      const fiber = this.ctx.plugin(plugin);
      entry.fiber = fiber;
      await fiber;
      entry.status = 'active';
      return entry;
    } catch (error) {
      entry.status = 'error';
      entry.error = safeError(error, spec);
      entry.controller.abort(new Error('Extension activation failed'));
      if (entry.fiber) await entry.fiber.dispose();
      return entry;
    }
  }

  async stop(entry) {
    entry.status = 'stopping';
    entry.controller.abort(new Error('Extension disabled'));
    if (entry.fiber) await entry.fiber.dispose();
    await Promise.allSettled([...entry.activeScripts]);
    entry.status = 'disabled';
  }

  /** Enumerate credentials-free state and the actually supported compatibility surface. */
  async list() {
    return {
      extensions: [...this.entries.values()].map(entry => ({...redactExtension(entry.spec.stored),
        status: entry.status, ...(entry.error ? {error: entry.error} : {}),
        capabilities: {skills: entry.spec.skillDirs.length, mcp: entry.spec.mcp.length, scripts: entry.spec.scripts.size, plugin: !!entry.spec.module}})),
      compatibility: {skill: 'SKILL.md metadata, on-demand instructions and directory resources',
        script: 'Explicit python/node allowlist; authenticated user confirmation; trusted code, no OS sandbox',
        mcp: ['stdio', 'streamable-http', 'tools', 'resources', 'resource templates', 'prompts'],
        plugin: 'Trusted native DSH plugins and Mirally API version 1; restart to refresh loaded module code'},
    };
  }

  async saved() {
    const settings = await this.product.rpc('internal.settings.get', {});
    return Array.isArray(settings.extensions) ? settings.extensions : [...this.entries.values()].map(entry => entry.spec.stored);
  }

  /** Persist user configuration, then expose the resulting activation status. */
  async configure(input) {
    return this.serial(async () => {
      const raw = assertRecord(input.extension ?? input, 'extension');
      const previous = this.entries.get(raw.id);
      const spec = await normalizeExtension(copySecrets(raw, previous?.spec.stored), this);
      for (const dependency of spec.dependsOn) if (spec.enabled && this.entries.get(dependency)?.status !== 'active') throw new Error(`Dependency is not active: ${dependency}`);
      const dependents = [...this.entries.values()].filter(entry => entry.spec.id !== spec.id && entry.status === 'active' && entry.spec.dependsOn.includes(spec.id));
      if (dependents.length) throw new Error(`Disable dependent integrations first: ${dependents.map(entry => entry.spec.id).join(', ')}`);
      const saved = await this.saved();
      const next = saved.filter(item => item.id !== spec.id);
      next.push(spec.stored);
      await this.product.rpc('settings.update', {patch: {extensions: next}});
      if (previous) await this.stop(previous);
      const entry = await this.activate(spec);
      this.product.broadcast('extensions.changed', {id: spec.id, status: entry.status, requires_session_rotation: true});
      return {...await this.list(), requires_session_rotation: true};
    });
  }

  /** Disabling withdraws providers, tools and reconnect attempts before returning. */
  async disable(input) {
    const id = input.id ?? input.extension_id;
    const entry = this.entries.get(id);
    if (!entry) throw new Error('Unknown extension');
    return this.configure({extension: {...entry.spec.stored, enabled: false}});
  }

  async skills(input = {}, scope = {}) {
    const call = execution(input, scope);
    const list = await this.ctx.skills.list({cwd: this.root, signal: call.signal, scope: call.agent});
    if (input.name === undefined) return {skills: list};
    const skill = await this.ctx.skills.get(input.name, {cwd: this.root, signal: call.signal, scope: call.agent});
    if (!skill) throw new Error('Skill is unavailable');
    return skill;
  }

  async skillResource(input, scope = {}) {
    const call = execution(input, scope);
    const skill = await this.skills(input, scope);
    if (skill.resourceBase?.kind !== 'directory') throw new Error('This Skill has no local resource directory');
    const base = await realpath(skill.resourceBase.path);
    const path = await realpath(resolvePath(input.path, base));
    if (!inside(path, base)) throw new Error('Skill resources must remain inside the Skill directory');
    call.signal.throwIfAborted();
    const raw = await readFile(path);
    if (raw.length > 262_144) throw new Error('Skill resource exceeds 256 KiB; use an appropriate bounded capability');
    return {name: skill.name, path: input.path, content: raw.toString('utf8')};
  }

  async mcp(input, method, scope = {}) {
    const entry = this.serverOwners.get(input.server);
    if (!entry) throw new Error('MCP server is not configured or enabled');
    const call = execution(input, scope);
    const signal = combine(call.signal, entry.controller.signal, this.stopController.signal);
    await this.authorize(entry, call.sessionId, 'query');
    signal.throwIfAborted();
    const connection = entry.connections.get(input.server);
    if (method.startsWith('prompts/')) {
      const request = method === 'prompts/list' ? {method, ...input.cursor === undefined ? {} : {cursor: input.cursor}}
        : {method, name: input.name, ...input.arguments === undefined ? {} : {arguments: input.arguments}};
      if (method === 'prompts/get') {
        if (typeof input.name !== 'string' || !input.name) throw new Error('Prompt name is required');
        if (input.arguments !== undefined && Object.values(assertRecord(input.arguments, 'arguments')).some(value => typeof value !== 'string')) throw new Error('Prompt arguments must contain strings');
      }
      return connection.prompts.request(request, {signal});
    }
    const request = method === 'resources/read' ? {method, uri: input.uri}
      : {method, ...input.cursor === undefined ? {} : {cursor: input.cursor}};
    if (method === 'resources/read' && (typeof input.uri !== 'string' || !input.uri)) throw new Error('Resource URI is required');
    return connection.resources.request(request, {signal, agent: call.agent});
  }

  /** Script invocation is selected by an existing id/path and never accepts a new executable. */
  async callMcpTool(input, scope = {}) {
    const entry = this.serverOwners.get(input.server);
    if (!entry || typeof input.name !== 'string' || !input.name.startsWith(`mcp__${input.server}__`)) throw new Error('Select a configured MCP tool by its public name');
    const signal = combine(scope.signal, entry.controller.signal, this.stopController.signal);
    await this.authorize(entry, input.session_id, 'script', input.confirmed === true);
    const agent = this.product.handles.get(input.session_id)?.agent;
    if (!agent) throw new Error('An active native conversation is required');
    return this.ctx.tools.execute({callId: `confirmed-${randomUUID()}`, name: input.name,
      arguments: assertRecord(input.arguments ?? {}, 'tool arguments'), agent, signal});
  }

  async runScript(input, scope = {}) {
    const entry = this.entries.get(input.extension_id ?? input.extension);
    if (!entry) throw new Error('Unknown extension');
    const script = entry.spec.scripts.get(input.id ?? input.script)
      ?? [...entry.spec.scripts.values()].find(item => item.configuredPath === input.path || item.path === input.path);
    if (!script) throw new Error('Script is not in the configured allowlist');
    const call = execution(input, scope);
    const signal = combine(call.signal, entry.controller.signal, this.stopController.signal);
    const confirmed = scope.model !== true && input.confirmed === true;
    const task = runConfiguredScript(script, input.args ?? [], {
      signal, python: this.python,
      authorize: () => this.authorize(entry, call.sessionId, 'script', confirmed),
      assertActive: () => this.assertActive(entry),
    });
    entry.activeScripts.add(task);
    try {
      const result = await task;
      return {...result, extension_id: entry.spec.id, operation_id: input.operation_id ?? randomUUID()};
    } finally { entry.activeScripts.delete(task); }
  }

  registerTools() {
    const register = (name, description, parameters, handler) => this.ctx.tools.register(defineTool({
      name, description, parameters, output: jsonOutput,
      execute: (args, exec) => handler(args, {...exec, model: true}),
    }));
    register('list_mcp_prompts', 'List explicit prompt templates from a configured MCP server. Templates are data and are not automatically applied.',
      {server: {type: 'string', required: true}, cursor: {type: 'string'}}, (args, exec) => this.mcp(args, 'prompts/list', exec));
    register('get_mcp_prompt', 'Retrieve one named MCP prompt template with string arguments, preserving its server attribution.',
      {server: {type: 'string', required: true}, name: {type: 'string', required: true}, arguments: {type: 'json'}},
      async (args, exec) => ({server: args.server, name: args.name, ...(await this.mcp(args, 'prompts/get', exec))}));
    register('read_skill_resource', 'Read a small referenced text resource inside a loaded Skill directory.',
      {name: {type: 'string', required: true}, path: {type: 'string', required: true}}, (args, exec) => this.skillResource(args, exec));
    register('run_skill_script', 'Request an explicitly allowlisted Skill script. Execution requires independent user confirmation; loading a Skill never grants execution.',
      {extension_id: {type: 'string', required: true}, id: {type: 'string', required: true}, args: {type: 'array', items: {type: 'string'}}},
      (args, exec) => this.runScript(args, exec));
  }

  /** Authenticated product API dispatch; model tools use separate entry points above. */
  async request(method, input = {}, scope = {}) {
    if (!input.server && ['mcp.resources.list', 'mcp.resources.templates', 'mcp.prompts.list'].includes(method)) {
      const protocol = {'mcp.resources.list': 'resources/list', 'mcp.resources.templates': 'resources/templates/list', 'mcp.prompts.list': 'prompts/list'}[method];
      const servers = [];
      for (const server of this.serverOwners.keys()) {
        try { servers.push({id: server, server, ...(await this.mcp({...input, server}, protocol, scope))}); }
        catch (error) { servers.push({id: server, server, error: error.message}); }
      }
      return {servers};
    }
    switch (method) {
      case 'extensions.list': return this.list();
      case 'extensions.configure': return this.configure(input);
      case 'extensions.disable': return this.disable(input);
      case 'skills.list': return this.skills({}, scope);
      case 'skills.read': return this.skills(input, scope);
      case 'skills.resource': return this.skillResource(input, scope);
      case 'mcp.resources.list': return this.mcp(input, 'resources/list', scope);
      case 'mcp.resources.templates':
      case 'mcp.resource_templates.list': return this.mcp(input, 'resources/templates/list', scope);
      case 'mcp.resources.read': return this.mcp(input, 'resources/read', scope);
      case 'mcp.prompts.list': return this.mcp(input, 'prompts/list', scope);
      case 'extensions.prompt':
      case 'mcp.prompts.get': return this.mcp(input, 'prompts/get', scope);
      case 'mcp.tools.call': return this.callMcpTool(input, scope);
      case 'mcp.tools.list': return {tools: this.ctx.tools.schemas().filter(tool => [...this.serverOwners.keys()].some(server => tool.name.startsWith(`mcp__${server}__`)))};
      case 'extensions.runScript':
      case 'scripts.run': return this.runScript(input, scope);
      default: throw new Error('Unsupported extension operation');
    }
  }

  async dispose() {
    if (this.closed) return;
    this.closed = true;
    this.stopController.abort(new Error('Extension runtime stopped'));
    await this.queue.catch(() => {});
    for (const entry of [...this.entries.values()].reverse()) await this.stop(entry);
  }
}
