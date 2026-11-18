// Identifies Mirally-authored assistant rows that are transcript bookkeeping,
// not provider model output. Some history surfaces keep gateway-injected rows
// visible, so use the narrower delivery-mirror predicate when visibility matters.
export const MIRALLY_TRANSCRIPT_ARTIFACT_API = "mirally-transcript" as const;
export const MIRALLY_TRANSCRIPT_ARTIFACT_PROVIDER = "mirally" as const;
export const MIRALLY_DELIVERY_MIRROR_MODEL = "delivery-mirror" as const;
export const CRON_DIRECT_DELIVERY_CONTEXT_KIND = "cron-direct-delivery-context" as const;
const MIRALLY_GATEWAY_INJECTED_MODEL = "gateway-injected" as const;

const TRANSCRIPT_ONLY_MIRALLY_ASSISTANT_MODELS = new Set<string>([
  MIRALLY_DELIVERY_MIRROR_MODEL,
  MIRALLY_GATEWAY_INJECTED_MODEL,
]);
const MIRALLY_DELIVERY_MIRROR_KINDS = new Set([
  "channel-final",
  "channel-final-suppressed",
  "message-tool-source-reply",
  CRON_DIRECT_DELIVERY_CONTEXT_KIND,
]);

function isMirallyDeliveryMirrorMarker(value: unknown): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const kind = (value as { kind?: unknown }).kind;
  return typeof kind === "string" && MIRALLY_DELIVERY_MIRROR_KINDS.has(kind);
}

export function isTranscriptOnlyMirallyAssistantModel(provider: unknown, model: unknown): boolean {
  return (
    provider === MIRALLY_TRANSCRIPT_ARTIFACT_PROVIDER &&
    typeof model === "string" &&
    TRANSCRIPT_ONLY_MIRALLY_ASSISTANT_MODELS.has(model)
  );
}

/**
 * Returns true when the message is an Mirally-authored transcript artifact
 * that must not be replayed to providers.
 *
 * Primary check: provider="mirally" + model in known transcript-only set.
 * Fallback: a valid mirallyDeliveryMirror marker catches observed historical
 * rows whose provider/model provenance was stripped (#99470).
 */
export function isTranscriptOnlyMirallyAssistantMessage(message: unknown): boolean {
  if (!message || typeof message !== "object" || Array.isArray(message)) {
    return false;
  }
  const entry = message as {
    role?: unknown;
    provider?: unknown;
    model?: unknown;
    mirallyDeliveryMirror?: unknown;
  };
  if (entry.role !== "assistant") {
    return false;
  }
  if (isTranscriptOnlyMirallyAssistantModel(entry.provider, entry.model)) {
    return true;
  }
  return isMirallyDeliveryMirrorMarker(entry.mirallyDeliveryMirror);
}

export function isMirallyMessageToolMirrorAssistantMessage(message: unknown): boolean {
  if (!message || typeof message !== "object" || Array.isArray(message)) {
    return false;
  }
  const entry = message as { role?: unknown; mirallyMessageToolMirror?: unknown };
  return entry.role === "assistant" && entry.mirallyMessageToolMirror !== undefined;
}

export function isMirallyDeliveryMirrorAssistantMessage(message: unknown): boolean {
  if (!message || typeof message !== "object" || Array.isArray(message)) {
    return false;
  }
  const entry = message as { role?: unknown; provider?: unknown; model?: unknown };
  return (
    entry.role === "assistant" &&
    entry.provider === MIRALLY_TRANSCRIPT_ARTIFACT_PROVIDER &&
    entry.model === MIRALLY_DELIVERY_MIRROR_MODEL
  );
}
