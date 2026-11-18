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

const external = /https?:\/\/[^\s"'<>]+/;

export function productCatalog(value) {
  if (typeof value === "string") {
    return value.split(new RegExp("(" + external.source + ")", "g"))
      .map((part, index) => index % 2 === 0
        ? part.replace(/\b(?:OpenClaw|Clawd(?:bot)?)\b/g, "Mirally")
        : part
      )
      .join("");
  }
  if (Array.isArray(value)) return value.map(productCatalog);
  if (!value || typeof value !== "object") return value;

  const entries = Object.entries(value).map(([key, entry]) => [renameKey(key), productCatalog(entry)]);
  if (new Set(entries.map(([key]) => key)).size !== entries.length) {
    throw new Error("Duplicate key in Mirally locale catalog");
  }
  return Object.fromEntries(entries);
}
