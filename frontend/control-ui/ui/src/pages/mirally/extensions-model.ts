/** Build the public extension contract; the runtime remains its validator and owner. */
export type ExtensionDraft = {
  id: string;
  type: "skill" | "mcp" | "plugin" | "advanced";
  path: string;
  server: string;
  transport: "stdio" | "streamable-http";
  command: string;
  args: string;
  cwd: string;
  url: string;
  secrets: string;
  readOnly: boolean;
  advanced: string;
};

export type RecordData = Record<string, any>;

export function newExtensionDraft(): ExtensionDraft {
  return {id: "", type: "skill", path: "", server: "", transport: "stdio", command: "",
    args: "", cwd: "", url: "", secrets: "", readOnly: false, advanced: ""};
}

export function parseObject(text: string, label: string): RecordData {
  const value = JSON.parse(text.trim() || "{}");
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label}须为 JSON 对象。`);
  return value;
}

/** Strip display-only runtime state before editing the persisted spec. */
export function editableExtension(value: RecordData): RecordData {
  const {status: _status, error: _error, capabilities: _capabilities, ...spec} = value;
  return spec;
}

export function buildExtension(draft: ExtensionDraft): RecordData {
  if (draft.type === "advanced") {
    const value = parseObject(draft.advanced, "扩展配置");
    if (typeof value.id !== "string" || !value.id.trim()) throw new Error("扩展配置缺少标识。 ");
    return value;
  }
  const id = draft.id.trim();
  if (!/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$/.test(id)) throw new Error("扩展标识须为 1–64 位字母、数字、点、下划线或连字符，并以字母或数字开头。");
  const common = {id, type: draft.type, enabled: true, version: "0.1.0"};
  if (draft.type === "skill" || draft.type === "plugin") {
    if (!draft.path.trim()) throw new Error("请输入目录或模块路径。");
    return {...common, path: draft.path.trim(), config: {apiVersion: 1}};
  }
  const config: RecordData = {serverName: draft.server.trim(), transport: draft.transport, readOnly: draft.readOnly};
  if (!/^[A-Za-z0-9_-]{1,32}$/.test(config.serverName)) throw new Error("MCP 服务名须为 1–32 位字母、数字、下划线或连字符。");
  const secrets = parseObject(draft.secrets, draft.transport === "stdio" ? "环境变量" : "请求头");
  if (Object.values(secrets).some(value => typeof value !== "string")) throw new Error("环境变量和请求头的值须为字符串。");
  if (draft.transport === "stdio") {
    if (!draft.command.trim()) throw new Error("请输入 MCP 启动命令。");
    // One argument per line preserves spaces and avoids shell tokenization.
    Object.assign(config, {command: draft.command.trim(), args: draft.args.split(/\r?\n/).filter(value => value !== ""), env: secrets});
    if (draft.cwd.trim()) config.cwd = draft.cwd.trim();
  } else {
    const url = new URL(draft.url.trim());
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) throw new Error("MCP 地址须为不含内嵌凭据的 HTTP(S) URL。");
    Object.assign(config, {url: url.href, headers: secrets});
  }
  return {...common, config};
}

export function extensionStatus(status: string): string {
  return ({active: "已启用", disabled: "已停用", starting: "正在启动", stopping: "正在停用", error: "启动失败"} as Record<string, string>)[status] ?? `未知状态：${status || "未返回"}`;
}

export function extensionType(type: string): string {
  return ({skill: "Skill", mcp: "MCP 服务", plugin: "本地插件", bundle: "组合扩展"} as Record<string, string>)[type] ?? type;
}

/** Required shapes fail visibly instead of fabricating an empty catalog. */
export function requireList(value: unknown, key: string): RecordData[] {
  if (!value || typeof value !== "object" || !Array.isArray((value as RecordData)[key])) throw new Error(`Mirally 未返回有效的 ${key} 列表。`);
  return (value as RecordData)[key];
}
