/** Add transient provider reasoning only to the current render, never persisted state. */
export function withLiveReasoningMessages(client: unknown, sessionKey: string, runId: string | null | undefined, history: unknown[]): unknown[] {
  const source = client as {getReasoningStream?: (session: string, run: string | null | undefined, messages: unknown[]) => unknown[]};
  const live = source?.getReasoningStream?.(sessionKey, runId, history) || [];
  return live.length ? [...history, ...live] : history;
}
