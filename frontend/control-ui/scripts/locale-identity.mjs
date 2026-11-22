// Convert the pinned catalog into Mirally's UI vocabulary before packaging.
function renameKey(key) {
  return key.replace(/OpenClaw|Openclaw|openClaw|OPENCLAW|openclaw/g, token => ({
    OpenClaw: "Mirally",
    Openclaw: "Mirally",
    openClaw: "mirally",
    OPENCLAW: "MIRALLY",
    openclaw: "mirally",
  })[token]);
}

const external = /https?:\/\/[^\s"'<>]+/g;
const retiredInstruction = /openclaw|clawdbot|discord\.gg\/clawd|\{(?:updateCommand|restartCommand)\}/i;

export function productCatalog(value) {
  if (typeof value === "string") {
    const copy = value.split(new RegExp("(" + external.source + ")", "g"))
      .map((part, index) => index % 2 === 0
        ? part.replace(/\b(?:OpenClaw|Clawd(?:bot)?)\b/g, "Mirally")
        : part
      )
      .join("");
    // Some inherited translations contain product-specific commands, routes
    // and configuration examples. Never advertise those as Mirally features.
    return retiredInstruction.test(copy) ? undefined : copy;
  }
  if (Array.isArray(value)) {
    return value.map(productCatalog).filter(entry => entry !== undefined);
  }
  if (!value || typeof value !== "object") return value;

  const entries = Object.entries(value)
    .map(([key, entry]) => [renameKey(key), productCatalog(entry)])
    .filter(([, entry]) => entry !== undefined);
  if (new Set(entries.map(([key]) => key)).size !== entries.length) {
    throw new Error("Duplicate key in Mirally locale catalog");
  }
  return Object.fromEntries(entries);
}
