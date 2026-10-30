/** Best-effort platform lifecycle signals. They do not gate chat or shutdown. */
export class WeixinLifecycle {
  constructor(channel) {this.channel=channel;this.queue=Promise.resolve();}
  notify(snapshot, active) {
    if (!snapshot?.installation_id || !snapshot?.token || !snapshot?.base_url) return;
    const {installation_id,revision,token,base_url}=snapshot;
    this.queue=this.queue.catch(()=>{}).then(async()=>{
      try {
        const current=await this.channel.state();
        if (!current.enabled || !current.bound || current.installation_id!==installation_id
            || current.revision!==revision || current.token!==token || current.base_url!==base_url) return;
        const api=this.channel.api(base_url);
        if (typeof api.notifyLifecycle !== 'function') return;
        await api.notifyLifecycle({token,active,signal:AbortSignal.timeout(1500)});
      } catch { /* Informational only; platform failure must not disrupt chat. */ }
    });
  }
}
