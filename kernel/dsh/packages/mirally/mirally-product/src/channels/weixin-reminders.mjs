/** Dispatch a reminder through the existing durable WeChat outbox.
 * The product owner and the transport keep separate receipts. */
const prefix = 'calendar-reminder:';

export async function notifyReminders(channel) {
  if (channel.closed) return;
  const state = await channel.state();
  if (!state.bound || !state.enabled || !state.context_token || channel.phase === 'login_required') return;
  const {deliveries} = await channel.product.rpc('calendar.delivery.list', {channel:'weixin'});
  for (const item of deliveries ?? []) {
    if (item.status !== 'pending') continue;
    await channel.authorize({...state, conversation_id:state.conversation_id});
    const destination = {installation_id:state.installation_id, revision:state.revision};
    const claimed = await channel.product.rpc('calendar.delivery.claim', {delivery_id:item.delivery_id, destination});
    if (!claimed.should_send) continue;
    const entry = claimed.delivery;
    try {
      await channel.authorize({...state, conversation_id:state.conversation_id});
      const current = await channel.product.rpc('calendar.delivery.get', {delivery_id:item.delivery_id});
      if (current.delivery?.status !== 'sending') continue;
      if (current.delivery?.eligible !== true) {
        await channel.product.rpc('calendar.delivery.ack', {delivery_id:item.delivery_id,claim_token:claimed.claim_token,status:'failed'});
        continue;
      }
      const text = `提醒：${entry.title}${entry.body && entry.body !== entry.title ? `\n${entry.body}` : ''}`;
      const {delivery} = await channel.store('enqueue', {installation_id:state.installation_id, revision:state.revision,
        key:prefix + item.delivery_id, text});
      if (['sent','failed','unknown','cancelled'].includes(delivery.status))
        await ackReminder(channel, {...delivery, key:prefix + item.delivery_id}, delivery.status);
    } catch (error) {
      // Enqueue or transport state can be uncertain. Do not issue a second send.
      await channel.product.rpc('calendar.delivery.ack', {delivery_id:item.delivery_id,
        claim_token:claimed.claim_token, status:'unknown'}).catch(() => {});
      if (error.code === 'weixin_scope_revoked') return;
      throw error;
    }
  }
}

export async function canDeliverReminder(channel, delivery) {
  if (!delivery.key?.startsWith(prefix)) return true;
  const {delivery:entry} = await channel.product.rpc('calendar.delivery.get', {delivery_id:delivery.key.slice(prefix.length)});
  return Boolean(entry?.status === 'sending' && entry.eligible === true && entry.destination?.installation_id === delivery.installation_id
    && entry.destination?.revision === delivery.revision);
}

export async function ackReminder(channel, delivery, status) {
  if (!delivery.key?.startsWith(prefix)) return;
  const id = delivery.key.slice(prefix.length);
  const {delivery:entry} = await channel.product.rpc('calendar.delivery.get', {delivery_id:id});
  if (entry?.status !== 'sending' || !entry.claim_token) return;
  await channel.product.rpc('calendar.delivery.ack', {delivery_id:id, claim_token:entry.claim_token,
    status:status === 'sent' ? 'sent' : ['failed','cancelled'].includes(status) ? 'failed' : 'unknown'});
}
