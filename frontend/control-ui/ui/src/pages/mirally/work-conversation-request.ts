/** This is an ordinary user turn in the current conversation; only the model
 * can decide its name and goal from the conversation and create the work. */
export const WORK_CONVERSATION_REQUEST =
  '请根据我们当前的对话整理一项工作并加入工作列表，结合已有内容确定简洁的名称和目标；如果已有对应工作，请继续维护。';

export function hasQueuedWorkConversationRequest(queue: readonly {text?: string}[] | undefined): boolean {
  return Boolean(queue?.some(item => item.text === WORK_CONVERSATION_REQUEST));
}
