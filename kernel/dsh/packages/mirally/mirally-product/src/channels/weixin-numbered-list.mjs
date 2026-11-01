/** Transient shortcuts for a list actually delivered to this bound Weixin route. */
// One wording per list kind. The chat channel re-exports this table so the menu,
// the expiry notice and the tests all use the same term ("编号", not "序号").
export const LIST_TEXT = {
  sessions: '会话编号已失效。发送“会话”重新查看。',
  works: '工作编号已失效。发送“我的工作”重新查看。',
  calendar: '日程编号已失效。发送“日程”重新查看。',
  other: '编号已失效，请重新查看工作详情或重新准备动作。',
};

export class WeixinNumberedLists {
  constructor(now = Date.now, text = LIST_TEXT) { this.now = now; this.text = text; this.pending = new Map(); this.active = new Map(); }

  stage(kind, event, ids) {
    const key = `${event.command_id}:control`;
    this.active.delete(kind);
    this.pending.set(kind, {key, installation_id: event.installation_id, revision: event.revision,
      conversation_id: event.conversation_id, ids: [...ids]});
    return key;
  }

  settle(key, sent) {
    for (const [kind, list] of this.pending) {
      if (list.key !== key) continue;
      this.pending.delete(kind);
      if (sent) this.active.set(kind, {...list, expires: this.now() + 10 * 60_000});
    }
  }

  select(kind, event, value) {
    if (!/^\d+$/.test(value)) return null;
    const list = this.active.get(kind);
    const index = Number(value);
    if (!list || list.expires <= this.now() || list.installation_id !== event.installation_id
      || list.revision !== event.revision || list.conversation_id !== event.conversation_id
      || !Number.isSafeInteger(index) || index < 1 || index > list.ids.length) {
      const message = this.text[kind] ?? this.text.other;
      throw Object.assign(new Error(message), {code: 'weixin_list_expired'});
    }
    return list.ids[index - 1];
  }
}
