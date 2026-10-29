/** Deterministic work controls. No natural-language command is an execution grant. */
import {randomInt} from 'node:crypto';
import {shortWorkId, workSummary} from '../work-host.mjs';

export const WORK_HELP = '工作入口：\n我的工作（/works）\n查看工作 1（/work 1）\n进入工作 1（/work-use 1）\n/work-status 当前会话关联的工作\n/work-run 1 {"script":"任务.py","arguments":[]} 准备运行\n/work-review 1 审阅待确认动作\n/work-approve 确认编号 批准已审阅的动作\n/work-cancel 1 停止运行\n/work-logs 1 [字节偏移] 查看日志\n/work-notify [on|off] 切换微信工作通知';
const plainLists = new Set(['我的工作怎么样了', '我的工作怎么样了？', '我的工作怎么样了?', '我的工作', '工作列表', '查看工作', '查看我的工作']);
const controlError = message => Object.assign(new Error(message), {code: 'invalid_work_control'});
// workSummary is shared with desktop internals; channel presentation must not
// ask a human to copy storage identifiers.
const channelSummary = (work, options = {}) => workSummary(work, options).split('\n')
  .map((line,index)=>index===0 ? String(work.title ?? '') : line)
  .filter(line=>!line.startsWith('/work ') && !line.startsWith('停止当前回复用 /stop'))
  .map(line=>line.replace(/^(最近运行：)(?:run_[a-zA-Z0-9_-]+)(（)/,'$1$2')).join('\n');

export class WeixinWorks {
  constructor(channel) { this.channel = channel; this.approvals = new Map(); }
  get product() { return this.channel.product; }
  request(method, params, event) { return this.product.workRequest(method, params, {...event, origin: 'weixin'}); }
  async reply(event, text) { await this.channel.reply(event, text, {control: true}); }
  confirmationDelivered(key, sent) {
    for (const entry of this.approvals.values()) if (entry.deliveryKey === key) entry.delivered = sent;
  }
  handles(text) { return /^\/(works|work(?:-[a-z]+)?)(?:\s|$)/.test(text) || plainLists.has(text.trim())
    || /^(我的工作|工作列表)[ \t]+[1-9]\d{0,3}$/.test(text)
    || /^(查看工作|进入工作|切换工作)[ \t]+\d+$/.test(text) || /^工作\s+.+\s+(?:现在怎样|进展|状态)[？?]?$/.test(text); }

  async resolve(value, event) {
    const selectedId = this.channel.numberedLists.select('works', event, value);
    if (selectedId) {
      const {work} = await this.request('work.get', {work_id: selectedId}, event);
      if (!work || work.intent === 'archived') throw controlError('这项工作已不可用，请发送“我的工作”重新查看。');
      return work;
    }
    const {works} = await this.request('work.list', {limit: 200}, event);
    const matches = works.filter(work => (work.work_id ?? work.id) === value || shortWorkId(work) === value || work.title === value);
    if (matches.length === 1) return matches[0];
    if (matches.length > 1) {
      const shown=matches.slice(0,8);this.channel.numberedLists.stage('works',event,shown.map(work=>work.work_id ?? work.id));
      throw controlError('有同名工作，请选择：\n' + shown.map((work,index)=>`${index+1}. ${work.title}`).join('\n') + '\n发送“查看工作 1”。');
    }
    throw controlError('未找到这项工作。发送 /works 查看编号。');
  }

  async review(event, work, approval) {
    const summary = approval?.spec;
    if (!summary || typeof summary !== 'object' || Array.isArray(summary)
      || typeof summary.script !== 'string' || !summary.script || !/^[a-f0-9]{64}$/.test(summary.script_sha256 ?? '')
      || !Array.isArray(summary.arguments) || !summary.arguments.every(value => typeof value === 'string')
      || typeof summary.cwd !== 'string' || !summary.cwd || !Array.isArray(summary.output_paths)
      || !summary.output_paths.every(value => typeof value === 'string')
      || !Number.isSafeInteger(summary.timeout_seconds) || summary.timeout_seconds < 1
      || !/^[a-f0-9]{64}$/.test(approval.digest ?? '') || approval.work_id !== (work.work_id ?? work.id)
      || !Number.isSafeInteger(approval.expires_at) || approval.expires_at <= this.channel.now())
      throw controlError('审批详情不完整或已失效，不能生成确认码；请在桌面重新准备。');
    if (Array.from(JSON.stringify(summary)).length > 10000)
      throw controlError('动作详情过长，无法在微信完整审阅；请缩小本次动作参数，或回电脑审阅。');
    for (const [key, entry] of this.approvals) if (entry.expires <= this.channel.now()) this.approvals.delete(key);
    if (this.approvals.size >= 100) this.approvals.delete(this.approvals.keys().next().value);
    let code;
    do { code=String(randomInt(100000000,1000000000)); } while (this.approvals.has(code));
    const expires = Math.min(Number(approval.expires_at ?? approval.expires_at_utc_ms) || this.channel.now() + 300000, this.channel.now() + 300000);
    this.approvals.set(code, {approval, expires, delivered:false, deliveryKey:`${event.command_id}:control`, installation_id: event.installation_id, revision: event.revision, conversation_id: event.conversation_id});
    await this.reply(event, `准备运行「${work.title}」，尚未执行。\n脚本：${JSON.stringify(summary.script)}\n指纹：${summary.script_sha256 ?? "请在桌面核对脚本"}\n参数：${JSON.stringify(summary.arguments ?? [])}\n目录：${JSON.stringify(summary.cwd)}\n输出：${JSON.stringify(summary.output_paths ?? [])}\n超时：${summary.timeout_seconds ?? 300} 秒\n脚本将以你的本机账户执行；目录范围不是系统沙箱。\n有效至 ${new Date(expires).toISOString()}（最多 5 分钟）。发送 /work-approve ${code} 批准本次具体动作。文件、工作或微信路由变化后需重新准备。`);
  }

  async control(event) {
    await this.channel.authorize(event);
    const text = event.text.trim();
    let [name, ...words] = text.split(/\s+/);
    let value = words.join(' ');
    if (plainLists.has(text)) { name = '/works'; value = ''; }
    const pageShortcut = /^(我的工作|工作列表)[ \t]+([1-9]\d{0,3})$/.exec(text);
    if (pageShortcut) { name = '/works'; value = pageShortcut[2]; }
    const shortcut = /^(查看工作|进入工作|切换工作)[ \t]+(\d+)$/.exec(text);
    if (shortcut) { name = shortcut[1] === '查看工作' ? '/work' : '/work-use'; value = shortcut[2]; }
    const natural = /^工作\s+(.+)\s+(?:现在怎样|进展|状态)[？?]?$/.exec(text);
    if (natural) { name = '/work'; value = natural[1]; }
    try {
      if (name === '/works') {
        if (value && !/^[1-9]\d{0,3}$/.test(value)) throw controlError('发送“我的工作”查看第一页，或发送“我的工作 2”查看下一页。');
        const page = value ? Number(value) : 1;
        const {works, has_more: hasMore} = await this.request('work.list', {limit: 30, offset: (page - 1) * 30}, event);
        await this.channel.authorize(event);
        this.channel.numberedLists.stage('works', event, works.map(work => work.work_id ?? work.id));
        const navigation = [page > 1 ? `上一页：我的工作 ${page - 1}` : '', hasMore ? `下一页：我的工作 ${page + 1}` : ''].filter(Boolean).join(' · ');
        await this.reply(event, works.length ? `你的工作 · 第 ${page} 页\n\n` + works.map((work, index) => {
          const title = Array.from(String(work.title ?? '').replace(/\s+/g, ' ')).slice(0, 120).join('');
          const [, status, summary] = workSummary(work).split('\n');
          return `${index + 1}. ${title}\n${status}${summary ? ` · ${Array.from(summary).slice(0, 160).join('')}` : ''}`;
        }).join('\n\n') + '\n\n发送“查看工作 1”查看状态，“进入工作 1”继续对话。' + (navigation ? `\n${navigation}` : '')
          : page > 1 ? `这一页没有工作。上一页：我的工作 ${page - 1}` : '目前没有工作。可以在对话中请 Mirally 整理并加入工作列表。');
      } else if (name === '/work-new') {
        await this.reply(event, '请在对话中告诉 Mirally 要开展或关注的工作，由它结合对话整理工作项。');
      } else if (name === '/work' || name === '/work-use') {
        const work = await this.resolve(value, event);
        const workId = work.work_id ?? work.id;
        if (name === '/work-use') {
          if (!work.conversation_id) throw controlError('这项工作尚未关联会话，请在桌面检查。');
          const current = await this.channel.authorize(event);
          if (current.conversation_id === work.conversation_id) {
            await this.reply(event, `当前已在工作「${work.title}」的对话中。`);
            return;
          }
          await this.product.cancelWeixinCommands(current.installation_id);
          const {state} = await this.channel.store('select', {expected_revision: current.revision, conversation_id: work.conversation_id});
          await this.reply({...event, revision: state.revision, conversation_id: state.conversation_id},
            `微信后续消息将继续工作「${work.title}」的主会话。\n${channelSummary(work)}`);
        } else {
          const result = await this.request('work.observe', {work_id: workId}, event);
          const detail = await this.request('work.get', {work_id: workId}, event);
          const pending = detail.work?.pending_approvals ?? [];
          const latestRun = detail.runs?.[0] ?? detail.work?.latest_run;
          if (latestRun) this.channel.numberedLists.stage('work-runs',event,[latestRun.run_id ?? latestRun.id]);
          if (pending.length) this.channel.numberedLists.stage('work-reviews',event,pending.slice(0,5).map(a=>({workId,approvalId:a.approval_id ?? a.id})));
          await this.reply(event, channelSummary(result.work, {...detail, observation: result.observation, detail: true})
            + (latestRun ? '\n查看日志：/work-logs 1 0\n停止运行：/work-cancel 1' : '')
            + (pending.length ? '\n待确认动作：\n' + pending.slice(0, 5).map((a,index) => `${index+1}. /work-review ${index+1}`).join('\n') : ''));
        }
      } else if (name === '/work-status') {
        const {works} = await this.request('work.list', {limit: 200}, event);
        const matches = works.filter(work => work.conversation_id === event.conversation_id);
        await this.reply(event, matches.length ? matches.map(work => channelSummary(work)).join('\n\n') : '当前会话未关联工作。发送 /works 查看，/work-use 编号 继续某项工作。');
      } else if (name === '/work-run') {
        const boundary = value.indexOf(' ');
        if (boundary < 0) throw controlError('用法：/work-run 工作编号 {"script":"任务.py","arguments":[]}\n脚本会以你的本机账户执行代码；请先审阅脚本。');
        const work = await this.resolve(value.slice(0, boundary), event);
        let spec;
        try { spec = JSON.parse(value.slice(boundary + 1)); } catch { throw controlError('运行参数应为 JSON 对象；不会按 shell 命令执行。'); }
        if (!spec || typeof spec !== 'object' || Array.isArray(spec) || Object.keys(spec).some(key => !['script', 'arguments', 'cwd', 'output_paths', 'timeout_seconds'].includes(key)))
          throw controlError('仅支持 script、arguments、cwd、output_paths 和 timeout_seconds 参数。');
        const {approval} = await this.request('work.run.prepare', {...spec, work_id: work.work_id ?? work.id, request_id: event.command_id}, event);
        await this.review(event, work, approval);
      } else if (name === '/work-review') {
        if (!value || /\s/.test(value)) throw controlError('请提供完整审批编号。用 /work 工作编号 查看待确认动作。');
        const selection=this.channel.numberedLists.select('work-reviews',event,value);
        let workId=selection?.workId, approvalId=selection?.approvalId ?? value;
        if (!workId) {
          const {works} = await this.request('work.list', {limit: 200}, event);
          const matches = works.flatMap(work => (work.pending_approvals ?? []).map(approval => ({work, approval})))
            .filter(item => (item.approval.approval_id ?? item.approval.id) === approvalId);
          if (matches.length !== 1) throw controlError('未找到当前待确认动作，可能已过期或处理；请重新查看工作。');
          workId=matches[0].work.work_id ?? matches[0].work.id;
        }
        const {work} = await this.request('work.get', {work_id:workId}, event);
        const approval = (work.pending_approvals ?? []).find(item => (item.approval_id ?? item.id) === approvalId);
        if (!approval) throw controlError('动作已变化或处理，请重新查看工作。');
        await this.review(event, work, approval);
      } else if (name === '/work-approve') {
        const entry = this.approvals.get(value);
        if (!entry || !entry.delivered || entry.expires <= this.channel.now() || entry.installation_id !== event.installation_id
          || entry.revision !== event.revision || entry.conversation_id !== event.conversation_id)
          throw controlError('确认码已失效或不属于当前微信入口，请重新准备运行。');
        const approvalId = entry.approval.approval_id ?? entry.approval.id;
        const {approval_token} = entry.approval_token ? entry : await this.request('work.run.approve', {approval_id: approvalId, expected_digest: entry.approval.digest}, event);
        entry.approval_token = approval_token;
        const {run} = await this.request('work.run.start', {approval_id: approvalId, approval_token,
          request_id: `weixin-start:${approvalId}`}, event);
        // Keep the exact receipt until expiry so a repeated confirmation gets
        // the same run, never a newly authorized action.
        this.channel.numberedLists.stage('work-runs',event,[run.run_id ?? run.id]);
        await this.reply(event, '运行已接纳。\n查看日志：/work-logs 1\n停止运行：/work-cancel 1');
      } else if (name === '/work-logs') {
        const [runSelector, rawOffset = '0', ...extra] = words;
        const runId=this.channel.numberedLists.select('work-runs',event,runSelector ?? '') ?? runSelector;
        if (!/^run_[a-f0-9]{32}$/.test(runId ?? '') || !/^\d+$/.test(rawOffset) || extra.length)
          throw controlError('用法：/work-logs 完整运行编号 [字节偏移]。从 /work 工作编号 获取可复制命令。');
        const offset = Number(rawOffset);
        if (!Number.isSafeInteger(offset) || offset < 0 || offset > 1024 * 1024)
          throw controlError('日志偏移须为 0 到 1048576 的整数。');
        const result = await this.request('work.run.logs', {run_id: runId, offset, limit: 4096}, event);
        if (typeof result.text !== 'string' || Array.from(result.text).length > 4096
          || !Number.isSafeInteger(result.next_offset) || result.next_offset < offset || result.next_offset > 1024 * 1024)
          throw controlError('日志响应不完整，请稍后重试；未改变任何运行。');
        this.channel.numberedLists.stage('work-runs',event,[runId]);
        await this.reply(event, `运行日志\n状态：${result.run?.status ?? result.run?.state ?? 'unknown'}\n字节范围：${offset}–${result.next_offset}\n`
          + (result.text || '此偏移当前暂无日志。')
          + (result.truncated ? '\n日志超过保存上限，部分内容未保留。' : '')
          + `\n下一偏移：${result.next_offset}\n继续或稍后复查：/work-logs 1 ${result.next_offset}`);
      } else if (name === '/work-notify') {
        if (words.length > 1 || !['', 'on', 'off'].includes(value))
          throw controlError('用法：/work-notify 查看；/work-notify on 开启，/work-notify off 关闭微信工作通知。');
        let {settings} = await this.request('work.settings.get', {}, event);
        if (!Number.isSafeInteger(settings?.revision) || typeof settings?.channels?.weixin !== 'boolean')
          throw controlError('未能读取当前通知设置，请稍后重试。');
        if (value && settings.channels.weixin !== (value === 'on')) {
          ({settings} = await this.request('work.settings.update', {expected_revision: settings.revision,
            patch: {channels: {weixin: value === 'on'}}}, event));
        }
        const categories = [['completed', '完成'], ['failed', '异常'], ['approval', '待确认']]
          .map(([key, label]) => `${label}：${settings.notifications?.[key] ? '开' : '关'}`).join('；');
        await this.reply(event, `微信工作通知：${settings.channels.weixin ? '已开启' : '已关闭'}\n${categories}\n`
          + '此开关只影响微信渠道，保留通知类别与其他渠道设置；关闭通知不会停止工作。\n'
          + `\n${settings.channels.weixin ? '关闭' : '开启'}微信通知：\n/work-notify ${settings.channels.weixin ? 'off' : 'on'}`);
      } else if (name === '/work-cancel') {
        if (!value || /\s/.test(value)) throw controlError('请提供要停止的完整运行编号；/stop 只停止回复。');
        const runId=this.channel.numberedLists.select('work-runs',event,value) ?? value;
        const {run} = await this.request('work.run.cancel', {run_id: runId}, event);
        await this.reply(event, `运行状态：${run.state ?? run.status}。取消由执行器核实；已发生的效果不会撤销。`);
      } else await this.reply(event, WORK_HELP);
    } catch (error) {
      if (error.code === 'weixin_scope_revoked' || error.code === 'work_scope_revoked') return;
      await this.reply(event, `工作操作未完成：${String(error.message || '请查看桌面状态').slice(0, 800)}`);
    }
  }
}
