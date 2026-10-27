/** Read-only presentation of the sole native transcript; no copied history store. */
import {toolDisplayValue, toolResultDisplay} from './tool-presentation.mjs';
import {createHash} from 'node:crypto';

export const toolPresentationId = (session, seq, id) => Number.isSafeInteger(seq)
  ? `mirally-tool:${session}:${seq}` : `mirally-tool:${session}:id:${id}`;
const textOf = content => typeof content === 'string' ? content
  : (content ?? []).filter(block => block?.type === 'text').map(block => block.text).join('');
const messageOf = event => event.data.message ?? event.data;

/** Only native user-origin references can authorize a history media read. */
export function nativeUserAttachments(events) {
  const result = [];
  for (const event of events) {
    const messages = event.type === 'user/message' ? [messageOf(event)]
      : event.type === 'agent/inbox/spliced' ? event.data.inserted ?? [] : [];
    for (const message of messages) {
      if (message.source?.kind !== 'user') continue;
      for (const block of Array.isArray(message.content) ? message.content : []) {
        if (['image', 'file'].includes(block?.type) && /^sha256:[a-f0-9]{64}$/.test(block.attachment?.attachmentId ?? ''))
          result.push({kind: block.type, ref: block.attachment});
      }
    }
  }
  return result;
}

function contentOf(content, role, conversationId) {
  if (typeof content === 'string') return [{type: 'text', text: content}];
  if (!Array.isArray(content)) return [];
  return content.flatMap(block => {
    if (block?.type === 'text' && typeof block.text === 'string') return [{type: 'text', text: block.text}];
    if (role === 'assistant' && block?.type === 'reasoning' && typeof block.text === 'string' && block.text)
      return [{type: 'thinking', thinking: block.text}];
    if (role !== 'user' || !['image', 'file'].includes(block?.type)) return [];
    const ref = block.attachment, label = ref?.name || (block.type === 'image' ? '图片材料' : '文件材料');
    if (!conversationId || !/^sha256:[a-f0-9]{64}$/.test(ref?.attachmentId ?? ''))
      return [{type: 'attachment_error', attachment: {kind: block.type === 'image' ? 'image' : 'document', label, code: 'file-not-found'}}];
    const url = `/api/sessions/${encodeURIComponent(conversationId)}/attachments/${block.type}/${ref.attachmentId.slice(7)}`;
    return block.type === 'image'
      ? [{type: 'image', url, fileName: label, mimeType: ref.mediaType, sizeBytes: ref.bytes, width: ref.width, height: ref.height}]
      : [{type: 'attachment', attachment: {kind: 'document', label, url, sizeBytes: ref.bytes}}];
  });
}

function toolArgs(raw) {
  let parsed = raw;
  if (typeof raw === 'string') {try {parsed = JSON.parse(raw);} catch { /* Preserve malformed native arguments as text. */ }}
  return toolDisplayValue(parsed).value;
}

export function projectMessages(events, commands = [], sequenceOffset = 0, {conversationId, nativeSessionId = 'native', weixinReceipts = []} = {}) {
  const result = [], seenUsers = new Set(), assistantByTurn = new Map();
  const commandIds = new Map(commands.filter(command => command.runtime_request_id).map(command => [command.runtime_request_id, command.command_id]));
  const calls = new Map(), callsByKey = new Map(), owners = new Map(), resultCalls = new Map();
  let owner;
  for (const event of events) {
    if (event.type === 'user/message' && messageOf(event).source?.kind === 'user') owner = commandIds.get(messageOf(event).id);
    if (['assistant/message', 'tool/call'].includes(event.type) && event.data.turn !== undefined && owner && !owners.has(event.data.turn))
      owners.set(event.data.turn, owner);
    if (event.type !== 'tool/call') continue;
    const call = {event, commandId: owner, id: toolPresentationId(nativeSessionId, event.seq, event.data.callId)};
    calls.set(event.seq, call);
    const key = JSON.stringify([event.data.turn, event.data.step, event.data.callId]);
    callsByKey.set(key, [...(callsByKey.get(key) ?? []), call]);
  }
  for (const event of events) {
    if (event.type !== 'tool/result') continue;
    const message = messageOf(event), id = message.toolCallId ?? message.source?.callId;
    let call = (event.sourceEventSeqs ?? []).map(seq => calls.get(seq)).find(Boolean);
    if (!call) {
      const candidates = callsByKey.get(JSON.stringify([event.data.turn, event.data.step, id])) ?? [];
      call = candidates.filter(item => item.event.seq < event.seq).at(-1) ?? (candidates.length === 1 ? candidates[0] : undefined);
    }
    if (call) {call.result = event; resultCalls.set(event.seq, call);}
  }
  const append = (message, event, role, commandId, extra = {}) => {
    if (role === 'user') {
      if (message.source?.kind !== 'user' || seenUsers.has(message.id)) return;
      seenUsers.add(message.id);
    }
    const content = extra.content ?? contentOf(message.content, role, conversationId), text = extra.text ?? textOf(message.content);
    if (!text && !content.length) return;
    const id = extra.id ?? message.id ?? `${nativeSessionId}:${event.seq}`, seq = sequenceOffset + event.seq + 1;
    let origin = role === 'user' && ['desktop', 'weixin', 'mobile'].includes(message.source?.mirally_input_origin)
      ? message.source.mirally_input_origin : undefined;
    if (role === 'user' && message.source?.mirally_input_origin === undefined && conversationId && commandId) {
      const expectedId = `mirally-${createHash('sha256').update(`${conversationId}:${commandId}`).digest('hex')}`;
      const digest = createHash('sha256').update(text).digest('hex');
      if (message.id === expectedId && weixinReceipts.some(receipt => receipt.command_id === commandId
        && receipt.runtime_request_id === message.id && receipt.session_id === nativeSessionId && receipt.content_hash === digest)) origin = 'weixin';
    }
    const projected = {id, role, text, content, seq, native_seq: event.seq, time: event.time, timestamp: event.time,
      ...(origin ? {input_origin: origin} : {}),
      ...(commandId ? {command_id: commandId} : {}),
      __openclaw: {id, seq, ...(commandId ? {runId: commandId, ...(role === 'user' ? {idempotencyKey: `${commandId}:user`} : {})} : {})}, ...extra};
    result.push(projected);
    if (role === 'assistant' && event.data.turn !== undefined) assistantByTurn.set(event.data.turn, projected);
  };
  let consumedCommand;
  for (const event of events) {
    if (event.type === 'agent/inbox/spliced') {
      for (const message of event.data.inserted ?? []) append(message, event, 'user', commandIds.get(message.id));
    } else if (event.type === 'user/message') {
      const message = messageOf(event);
      if (message.source?.kind === 'user') consumedCommand = commandIds.get(message.id);
      append(message, event, 'user', commandIds.get(message.id));
    } else if (event.type === 'assistant/message') append(messageOf(event), event, 'assistant', consumedCommand);
    else if (event.type === 'tool/call' || event.type === 'tool/result' && !resultCalls.has(event.seq)) {
      const call = calls.get(event.seq), final = call?.result ?? (event.type === 'tool/result' ? event : null);
      const message = final ? messageOf(final) : {}, id = call?.id ?? toolPresentationId(nativeSessionId, event.seq, message.toolCallId);
      const name = call?.event.data.name ?? 'tool', content = [];
      if (call) content.push({type: 'toolcall', id, name, arguments: toolArgs(call.event.data.arguments)});
      if (final) content.push({type: 'toolresult', id, name, text: toolResultDisplay(message).value.content[0].text, isError: message.isError === true});
      append({}, event, 'toolResult', call?.commandId ?? owners.get(event.data.turn), {id, text: '', content, toolCallId: id});
    } else if (event.type === 'turn/end') {
      const terminal = assistantByTurn.get(event.data.turn);
      if (terminal) {
        terminal.__openclaw.runTerminal = true;
        terminal.stopReason = ({completed: 'stop', 'max-tokens': 'length', aborted: 'aborted', error: 'error'})[event.data.reason?.kind] ?? 'stop';
      }
    }
  }
  return result;
}
