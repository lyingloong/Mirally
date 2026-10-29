type Delivery = {delivery_id:string; status:string; title:string; body:string; claim_token?:string};
type Rpc = (method:string, params?:Record<string,unknown>) => Promise<any>;
type NativeBridge = {notifyReminder?: (title:string, body:string) => Promise<boolean>};

/** The native shell is the desktop delivery endpoint; browser tabs never claim it. */
export class DesktopReminders {
  private timer?: ReturnType<typeof setInterval>;
  private running = false;
  private closed = false;
  constructor(private rpc:Rpc, private bridge:()=>NativeBridge | undefined) {}
  start() { if(this.timer)return; this.closed=false; void this.tick(); this.timer=setInterval(()=>void this.tick(),5000); }
  stop() { this.closed=true; clearInterval(this.timer); this.timer=undefined; }
  private async tick() {
    const api=this.bridge();
    if(this.closed || this.running || typeof api?.notifyReminder !== 'function') return;
    this.running=true;
    try {
      const {deliveries}=await this.rpc('calendar.delivery.list',{channel:'desktop'});
      for(const item of deliveries as Delivery[]) {
        if(this.closed || item.status!=='pending') continue;
        const claim=await this.rpc('calendar.delivery.claim',{delivery_id:item.delivery_id});
        if(!claim.should_send) continue;
        const token=claim.claim_token as string;
        try {
          const current=await this.rpc('calendar.delivery.get',{delivery_id:item.delivery_id});
          if(current.delivery?.status!=='sending') continue;
          if(this.closed || current.delivery?.eligible!==true) {
            await this.rpc('calendar.delivery.ack',{delivery_id:item.delivery_id,claim_token:token,status:'failed'}); continue;
          }
          const shown=await api.notifyReminder(String(claim.delivery.title),String(claim.delivery.body));
          await this.rpc('calendar.delivery.ack',{delivery_id:item.delivery_id,claim_token:token,status:shown?'sent':'failed'});
        } catch {
          // The native request may have taken effect before a lost response.
          await this.rpc('calendar.delivery.ack',{delivery_id:item.delivery_id,claim_token:token,status:'unknown'}).catch(()=>{});
        }
      }
    } catch { /* Pending deliveries remain visible and may be retried safely. */ }
    finally {this.running=false;}
  }
}
