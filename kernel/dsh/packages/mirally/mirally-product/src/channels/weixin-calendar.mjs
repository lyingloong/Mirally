/** Explicit calendar shortcuts for a bound WeChat channel. */
const fail = message => Object.assign(new Error(message), {code:'calendar_control'});
const day = (now, zone, shift = 0) => {
  const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(now)).map(x=>[x.type,x.value]));
  const date=new Date(Date.UTC(Number(parts.year),Number(parts.month)-1,Number(parts.day)+shift));
  return date.toISOString().slice(0,10);
};
const shown = (event,zone) => new Intl.DateTimeFormat('zh-CN',{timeZone:zone,month:'numeric',day:'numeric',weekday:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(event.start_at_utc_ms));

export class WeixinCalendar {
  constructor(channel) {this.channel=channel;}
  handles(text) { return /^(日程|今日日程|明日日程|\/calendar(?:\s+(?:today|tomorrow))?|(?:查看日程|进入日程|\/event|\/event-use)\s+\d+)$/.test(text.trim()); }
  async control(event) {
    const channel=this.channel, text=event.text.trim();
    try {
      const current=await channel.authorize(event);
      const settings=await channel.product.rpc('calendar.settings.get');
      if (!settings.confirmed || !settings.time_zone) throw fail('请先在电脑的通用设置中确认时区。');
      const selection=/^(查看日程|进入日程|\/event|\/event-use)\s+(\d+)$/.exec(text);
      if (selection) {
        const id=channel.numberedLists.select('calendar',event,selection[2]);
        const {event:item}=await channel.product.rpc('calendar.event.get',{event_id:id});
        await channel.authorize(event);
        if (!item || item.status!=='active') throw fail('这项日程已变化，请重新查看日程。');
        if (['进入日程','/event-use'].includes(selection[1])) {
          if (!item.conversation_id) throw fail('这项日程没有关联会话。');
          if (item.conversation_id===current.conversation_id) {await channel.reply(event,`当前已在「${item.title}」的对话中。`,{control:true});return;}
          if (current.mode === 'assistant' && channel.hasActiveInput?.(current.installation_id)) {await channel.reply(event,'当前微信会话正在处理，请结束后切换日程。',{control:true});return;}
          await channel.product.cancelWeixinCommands(current.installation_id);
          const {state}=await channel.store('select',{expected_revision:current.revision,conversation_id:item.conversation_id});
          await channel.reply({...event,revision:state.revision,conversation_id:state.conversation_id},`已进入「${item.title}」的对话。`,{control:true});return;
        }
        const reminders=(item.reminder_offsets_minutes??[]).map(n=>n===0?'开始时':`提前 ${n} 分钟`).join('、');
        await channel.reply(event,`${item.title}\n${shown(item,settings.time_zone)}${item.notes?`\n${Array.from(item.notes).slice(0,1200).join('')}`:''}${reminders?`\n提醒：${reminders}`:''}${item.conversation_id?`\n进入日程 ${selection[2]}，继续相关对话。`:''}`,{control:true});return;
      }
      let start_at, end_at, label;
      if (text==='今日日程' || text==='/calendar today' || text==='明日日程' || text==='/calendar tomorrow') {
        const offset=(text==='明日日程'||text==='/calendar tomorrow')?1:0;
        start_at=(await channel.product.rpc('calendar.time.resolve',{local_time:`${day(channel.now(),settings.time_zone,offset)}T00:00`})).iso;
        end_at=(await channel.product.rpc('calendar.time.resolve',{local_time:`${day(channel.now(),settings.time_zone,offset+1)}T00:00`})).iso;
        label=offset?'明日日程':'今日日程';
      } else {start_at=new Date(channel.now()).toISOString();label='即将到来的日程';}
      const {events}=await channel.product.rpc('calendar.event.list',{start_at,...(end_at?{end_at}:{}),limit:21});
      await channel.authorize(event);
      const rows=events.slice(0,20);channel.numberedLists.stage('calendar',event,rows.map(item=>item.event_id));
      const list=rows.map((item,index)=>`${index+1}. ${shown(item,settings.time_zone)} · ${Array.from(String(item.title).replace(/\s+/g,' ')).slice(0,120).join('')}`).join('\n');
      await channel.reply(event,rows.length?`${label}\n\n${list}\n\n查看日程 1 · 进入日程 1${events.length>20?'\n更多日程请在电脑查看。':''}`:`${label}：暂无。`,{control:true});
    } catch(error) {
      if(error.code==='weixin_scope_revoked') throw error;
      await channel.reply(event,error.message||'日程暂时无法读取，请稍后重试。',{control:true});
    }
  }
}
