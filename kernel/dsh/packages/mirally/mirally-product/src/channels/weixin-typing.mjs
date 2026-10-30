/** Optional, best-effort typing indicators. These are not execution receipts. */
export class WeixinTyping {
  constructor(channel, {intervalMs=8000}={}) {this.channel=channel;this.intervalMs=intervalMs;this.entries=new Set();this.closed=false;}

  begin(event) {
    if(this.closed)return () => {};
    const entry={event,live:true,ticket:null,timer:null,inflight:false,started:false};
    this.entries.add(entry);
    void this.start(entry).catch(()=>this.retire(entry));
    return () => this.retire(entry);
  }

  async start(entry) {
    const enabled=(await this.channel.product.rpc('settings.get')).weixin_typing_enabled===true;
    if(!enabled || !entry.live || this.closed)return this.retire(entry);
    const state=await this.channel.authorize(entry.event);
    if(!state.token || !state.peer_id || !entry.event.context_token)return this.retire(entry);
    const api=this.channel.api(state.base_url), signal=this.channel.connection?.signal;
    const {typing_ticket}=await api.getTypingTicket({token:state.token,to:state.peer_id,contextToken:entry.event.context_token,signal});
    if(!entry.live || this.closed)return this.retire(entry);
    entry.ticket=typing_ticket;entry.api=api;
    await this.pulse(entry);
  }

  async pulse(entry) {
    if(!entry.live || this.closed || entry.inflight)return;
    entry.inflight=true;
    try {
      await this.channel.authorize(entry.event);
      if(!entry.live || this.closed)return;
      const settings=await this.channel.product.rpc('settings.get');
      if(settings.weixin_typing_enabled!==true) {this.retire(entry);return;}
      if(!entry.live || this.closed)return;
      const current=await this.channel.authorize(entry.event);
      if(!entry.live || this.closed)return;
      await entry.api.sendTyping({token:current.token,to:current.peer_id,ticket:entry.ticket,active:true,signal:this.channel.connection?.signal});
      entry.started=true;
    } catch {this.retire(entry);}
    finally {
      entry.inflight=false;
      if(!entry.live || this.closed) void this.finish(entry);
      else {entry.timer=setTimeout(()=>void this.pulse(entry),this.intervalMs);entry.timer.unref?.();}
    }
  }

  retire(entry) {
    if(!entry.live)return;
    entry.live=false;clearTimeout(entry.timer);
    if(!entry.inflight) void this.finish(entry);
  }

  async finish(entry) {
    if(entry.finished)return;
    entry.finished=true;this.entries.delete(entry);
    if(!entry.started || this.closed || [...this.entries].some(other=>other.live && other.event.installation_id===entry.event.installation_id))return;
    try {
      const state=await this.channel.authorize(entry.event);
      if(this.closed || [...this.entries].some(other=>other.live && other.event.installation_id===entry.event.installation_id))return;
      await entry.api.sendTyping({token:state.token,to:state.peer_id,ticket:entry.ticket,active:false,signal:this.channel.connection?.signal});
    } catch { /* A stopped or rebound channel no longer has authority to send. */ }
  }

  stop() {this.closed=true;for(const entry of this.entries){entry.live=false;clearTimeout(entry.timer);}this.entries.clear();}
}
