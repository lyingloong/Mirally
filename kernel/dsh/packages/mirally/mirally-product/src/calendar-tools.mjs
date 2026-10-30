import {createHash} from 'node:crypto';

const output = {schema:{type:'object',additionalProperties:true},render:(_args,result)=>[{type:'text',text:JSON.stringify(result)}]};
const object = properties => ({type:'object',properties,additionalProperties:false});
const text = {type:'string'};
const offsets = {type:'array',items:{type:'integer',minimum:0,maximum:525600}};

/** All mutations pass through product ownership and the current command scope. */
export function installCalendarTools(host) {
  const reg = (name,description,parameters,action) => host.ctx.tools.register({name,description,parameters,output,
    execute:async(args,exec)=>{
      exec.signal.throwIfAborted();
      const owner=host.nativeRuntime.owner(exec.agent);
      if(!owner || owner.rootSessionId!==exec.agent.id || !owner.commandId) throw new Error('请在当前主会话中管理日程。');
      const policy=host.commandPolicy(exec.agent);
      await host.checkCommandScope(exec.agent,name); await host.guard(exec.agent.id);
      await host.checkCommandScope(exec.agent,name); exec.signal.throwIfAborted();
      if(host.commandPolicy(exec.agent)!==policy || host.consumedCommandId(exec.agent)!==owner.commandId)
        throw Object.assign(new Error('会话或授权已变化，请重新发起。'),{code:'calendar_scope_revoked'});
      const scope=policy?.origin==='weixin' && !policy.schedule_id ? {installation_id:policy.installation_id,revision:policy.revision,command_id:policy.command_id,
        ...(policy.target_conversation_id ? {source_conversation_id:policy.conversation_id,source_command_id:policy.source_command_id} : {})} : undefined;
      const request_id=`${owner.commandId}:${name}:${createHash('sha256').update(JSON.stringify(args)).digest('hex')}`;
      return action(args,{conversation_id:owner.conversation.conversation_id,request_id,scope});
    }});
  const resolve = async value => value == null ? null : host.rpc('calendar.time.resolve',{local_time:value});
  reg('mirally_calendar_time','Get the current date, time and confirmed product timezone before interpreting relative dates. If unconfirmed, ask the user to confirm it in settings.',object({}),async()=>{
    const settings=await host.rpc('calendar.settings.get'); const now=new Date();
    const local=settings.confirmed ? new Intl.DateTimeFormat('sv-SE',{timeZone:settings.time_zone,dateStyle:'short',timeStyle:'medium'}).format(now) : null;
    return {now_utc:now.toISOString(),local_time:local,...settings};
  });
  reg('mirally_calendar_events','List real calendar events. Do not invent events or their completion.',object({}),async()=>host.rpc('calendar.event.list',{limit:100}));
  reg('mirally_create_event','Create a calendar event explicitly requested by the user. Use mirally_calendar_time first. start_local and optional end_local are local clock times in the confirmed product timezone (YYYY-MM-DDTHH:mm), with no timezone offset. Do not invent an end time. reminder_offsets_minutes contains minutes before the start; omit it if no reminder was requested.',
    {...object({title:text,notes:text,start_local:text,end_local:text,all_day:{type:'boolean'},reminder_offsets_minutes:offsets}),required:['title','start_local']},async(args,ctx)=>{
      const start=await resolve(args.start_local), end=await resolve(args.end_local);
      if (end && end.time_zone!==start.time_zone) throw new Error('时区已改变，请重新安排时间。');
      const result=await host.rpc('calendar.event.create',{title:args.title,start_at:start.iso,expected_time_zone:start.time_zone,...(args.notes!==undefined?{notes:args.notes}:{}),...(end?{end_at:end.iso}:{}),
        ...(args.all_day!==undefined?{all_day:args.all_day}:{}),...(args.reminder_offsets_minutes?{reminder_offsets_minutes:args.reminder_offsets_minutes}:{}),
        conversation_id:ctx.conversation_id,request_id:ctx.request_id,...(ctx.scope?{weixin_scope:ctx.scope}:{})});
      host.broadcast('calendar',{action:'created'}); return result;
    });
  reg('mirally_update_event','Change an explicitly selected event, using its event_id and current version from mirally_calendar_events. Times in patch use start_local/end_local in the confirmed product timezone.',
    {...object({event_id:text,expected_version:{type:'integer'},patch:object({title:text,notes:text,start_local:text,end_local:text,all_day:{type:'boolean'},reminder_offsets_minutes:offsets})}),required:['event_id','expected_version','patch']},async(args,ctx)=>{
      const {start_local,end_local,...patch}=args.patch;
      const start=await resolve(start_local), end=await resolve(end_local);
      if(start) patch.start_at=start.iso;
      if(end) patch.end_at=end.iso;
      if(start && end && start.time_zone!==end.time_zone) throw new Error('时区已改变，请重新安排时间。');
      const zone=start?.time_zone ?? end?.time_zone;
      const result=await host.rpc('calendar.event.update',{event_id:args.event_id,expected_version:args.expected_version,patch,...(zone?{expected_time_zone:zone}:{}),...(ctx.scope?{weixin_scope:ctx.scope}:{})});
      host.broadcast('calendar',{action:'updated'}); return result;
    });
  reg('mirally_cancel_event','Cancel an explicitly selected event using the event_id and current version from mirally_calendar_events.',
    {...object({event_id:text,expected_version:{type:'integer'}}),required:['event_id','expected_version']},async(args,ctx)=>{
      const result=await host.rpc('calendar.event.cancel',{...args,...(ctx.scope?{weixin_scope:ctx.scope}:{})});
      host.broadcast('calendar',{action:'cancelled'}); return result;
    });
  reg('mirally_remind_at','Create a standalone reminder explicitly requested by the user. due_local is YYYY-MM-DDTHH:mm in the confirmed product timezone, with no offset; use mirally_calendar_time to resolve relative dates. Report success only when the tool succeeds.',
    {...object({title:text,body:text,due_local:text}),required:['title','due_local']},async(args,ctx)=>{
      const due=await resolve(args.due_local);
      return host.rpc('calendar.reminder.create',{title:args.title,body:args.body??args.title,due_at:due.iso,expected_time_zone:due.time_zone,
        conversation_id:ctx.conversation_id,request_id:ctx.request_id,...(ctx.scope?{weixin_scope:ctx.scope}:{})});
    });
  reg('mirally_repeating_reminders','List active repeating reminders.',object({}),async()=>host.rpc('calendar.recurrence.list'));
  reg('mirally_repeat_reminder','Create a daily or weekly reminder in the confirmed product timezone. weekday uses Monday=0 through Sunday=6; local_time is HH:MM. Ask if the time is unspecified.',
    {...object({title:text,body:text,frequency:{type:'string',enum:['daily','weekly']},weekday:{type:'integer',minimum:0,maximum:6},local_time:text}),required:['title','frequency','local_time']},async(args,ctx)=>{
      const settings=await host.rpc('calendar.settings.get');
      return host.rpc('calendar.recurrence.create',{...args,expected_time_zone:settings.time_zone,conversation_id:ctx.conversation_id,request_id:ctx.request_id,...(ctx.scope?{weixin_scope:ctx.scope}:{})});
    });
  reg('mirally_cancel_repeating_reminder','Cancel a repeating reminder by its recurrence ID.',
    {...object({recurrence_id:text}),required:['recurrence_id']},async(args,ctx)=>host.rpc('calendar.recurrence.cancel',{...args,...(ctx.scope?{weixin_scope:ctx.scope}:{})}));
  reg('mirally_cancel_reminder','Cancel an explicitly identified reminder. Find its identity with mirally_reminders.',
    {...object({commitment_id:text}),required:['commitment_id']},async(args,ctx)=>host.rpc('calendar.reminder.cancel',{
      commitment_id:args.commitment_id,...(ctx.scope?{weixin_scope:ctx.scope}:{})}));
}
