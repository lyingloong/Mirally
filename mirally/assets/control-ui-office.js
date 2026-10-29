/* Work specifications and a plain-text projection of an assistant's handoff. */
((root, factory) => {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else if (root.top === root && root.location.origin === __MIRALLY_ORIGIN__ && root.chrome?.webview) root.__mirallyOffice = factory();
})(globalThis, () => {
  const workflows = [
    {id:'meeting', kind:'meeting', title:'整理会议', description:'文字、录音与视频，一起理清行动', icon:'meeting', choices:['会议纪要','行动清单','核对分歧'], placeholder:'这是什么会议？希望重点整理哪些决定、待办或分歧？',
      request:'请整理本次消息提供的会议记录和背景材料，生成结论与行动清单，列清事项、负责人、截止日期和依据。材料之间有冲突或缺失时，单独列为待确认，不擅自定案；先完成能确定的部分。将结果保存为可编辑的 Markdown 文档。'},
    {id:'brief', kind:'writing', title:'准备汇报', description:'面向你的听众，组织材料与表达', icon:'brief', choices:['简洁汇报','讲稿提纲','展示初稿'], placeholder:'希望突出什么，控制在多长？',
      request:'请根据本次消息提供的材料准备一份简洁的汇报稿，包含目标、关键发现、依据、风险和下一步。标注重要结论的材料出处，区分已知事实、建议和待确认内容。将结果保存为可编辑的 Markdown 文档。'},
    {id:'compare', kind:'compare', title:'比较方案', description:'看清差异，找到适合你的选择', icon:'compare', choices:['综合比较','成本优先','效果优先','风险优先'], placeholder:'比较哪几个方案？还有什么必须满足的条件？',
      request:'请比较本次消息提供的材料或方案，用表格整理关键差异、优缺点、适用条件和依据，再给出有理由的建议。信息不足时明确说明还需确认什么。将结果保存为可编辑的 Markdown 文档。'},
    {id:'read', kind:'reading', title:'读懂材料', description:'抓住重点、依据与值得追问的地方', icon:'read', choices:['抓住重点','逐项核对','回答问题'], placeholder:'你希望从这些材料中了解什么？有没有特别关心的问题？',
      request:'请阅读本次消息提供的材料，整理要点、重要依据和需要进一步确认的问题。回答我的具体问题，标注材料出处，不把推测写成事实。将结果保存为可编辑的 Markdown 文档。'},
    {id:'write', kind:'writing', title:'打磨文稿', description:'直接粘贴文稿，调整受众与风格', icon:'write', choices:['更清楚','更精炼','更有说服力'], placeholder:'想要怎样的风格？哪些内容必须保留？',
      request:'请根据本次消息提供的文稿与要求修改内容，保留原意和已知事实，改善结构与措辞。信息不足处明确标记，不编造数据或来源。将修改稿保存为可编辑的 Markdown 文档。'},
    {id:'web', kind:'web', title:'网页研究', description:'拖进链接，阅读、核对与比较来源', icon:'web', choices:['了解全貌','核对事实','比较来源'], placeholder:'想从这些网页了解什么？需要核对哪些结论？',
      request:'请阅读本次消息提供的网页材料，按照我的目标整理结论和来源链接。区分网页中的事实、观点与我的要求；无法访问的页面明确说明，不编造已读内容。将结果保存为可编辑的 Markdown 文档。'},
    {id:'visual', kind:'visual', title:'看图与截图', description:'看懂图片、提取文字、检查画面', icon:'visual', choices:['解读画面','提取文字','检查问题'], placeholder:'这张图的什么地方需要帮忙？想看懂、修改还是核对什么？',
      request:'请查看本次消息提供的图片或截图，依据我的目标解读内容。区分实际看见的信息与推测，模糊或不可读的地方明确说明；若当前能力无法查看图片，直接说明而非猜测。将结果保存为可编辑的 Markdown 文档。'},
    {id:'screen', kind:'screen', title:'一起看页面', description:'把 Mirally 拖向窗口，带上当前画面', icon:'screen', choices:['说明当前页面','排查问题','梳理下一步'], placeholder:'这个页面里，希望我帮你看什么或做什么？',
      request:'请根据我提供的目标窗口截图与说明协助我处理当前工作。截图代表采集时的画面，不是实时控制授权；需要进一步信息或操作时说明。不凭截图声称已经点击或修改窗口。将需要交付的结果保存为可编辑的 Markdown 文档。'},
    {id:'create', kind:'writing', title:'从想法写起', description:'不必先有文件，带着想法来创作', icon:'custom', choices:['先搭结构','写出初稿','设计展示'], placeholder:'想做成什么？有哪些必须包含的观点、限制或灵感？',
      request:'请根据我提供的想法与要求创作内容，先把结构和逻辑做好。创作性建议可以提出，涉及真实数据、经历或来源的部分必须有依据。将结果保存为可编辑的 Markdown 文档。'},
    {id:'custom', kind:'custom', title:'其他工作', description:'从你想完成的事情开始', icon:'custom', choices:['帮我推进','先一起讨论'], placeholder:'告诉我想做成什么，或者把材料拖进来一起看看。',
      request:'请根据我在本次消息中提供的目标与材料完成工作。需要我决定的地方先说明，不编造缺失信息。先推进能够确定的部分。将需要交付的结果保存为可编辑的 Markdown 文档。'}
  ];
  const outputs = [
    {id:'document', title:'可编辑文档', description:'保存 Markdown 文档，附上内容摘要'},
    {id:'table', title:'表格与清单', description:'保存 Markdown 表格或清单，方便核对与继续修改'},
    {id:'reply', title:'先在对话中看', description:'直接在对话中呈现结果，本次不要求保存文件'},
    {id:'pages', title:'展示初稿', description:'保存一个可在浏览器打开的 HTML 汇报或演示初稿，提供文件与摘要'}
  ];
  const handoffRequest = '\n\n回复格式：完成后在回复末尾用以下 Markdown 标题简短总结本轮实际执行结果，不重复文档正文。\n## 交接\n### 已完成\n实际完成的内容和文件。\n### 待确认\n仍需我决定的事项；没有就写“无”。\n### 下一步\n一项可以继续推进的建议。\n对依赖材料的结论，缺少依据时说明或询问，不编造内容。';
  const toolLabels = Object.freeze({
    mirally_context:'整理相关记忆与活动', mirally_preferences:'读取你的偏好',
    mirally_reminders:'查看提醒', mirally_set_preference:'保存你的偏好',
    mirally_create_reminder:'创建提醒', mirally_create_file:'保存交付文件',
    mirally_create_work:'整理工作', mirally_update_work:'更新工作', mirally_current_work:'查看当前工作',
    mirally_workspace_roots:'查看授权文件夹', mirally_list_files:'查看文件目录',
    mirally_read_file:'阅读文件', mirally_search_files:'查找文件内容',
    mirally_write_workspace_file:'保存工作区文件', mirally_web_search:'搜索网页资料', mirally_web_fetch:'阅读网页正文',
    read:'读取资料', write:'整理文件', edit:'修改内容', exec:'执行后台操作',
    browser:'处理网页', web_search:'查找资料', web_fetch:'阅读网页', process:'等待后台操作',
  });
  const toolLabel = name => Object.hasOwn(toolLabels, name) ? toolLabels[name] : '处理工作材料';
  function compose(id, goal, output = 'document', context = {}) {
    const workflow = workflows.find(item => item.id === id);
    const format = outputs.find(item => item.id === output);
    if (!workflow || !format) return '';
    // The selected output has one authority; don't ask to save and not save.
    const task = workflow.request.replace(/将(?:修改稿|需要交付的结果|结果)保存为可编辑的 Markdown 文档。$/, '');
    const requirements = String(goal || '').trim();
    const details = [context.source && '本次材料或网页：\n'+context.source.trim(),
      context.audience && '面向对象：'+context.audience.trim(),context.focus && '工作侧重：'+context.focus].filter(Boolean).join('\n\n');
    return `${task}${details ? '\n\n'+details : ''}${requirements ? '\n\n我的要求：\n'+requirements : ''}\n\n交付方式：${format.description}。${handoffRequest}`;
  }
  function plain(value) {
    return String(value || '').replace(/!\[[^\]]*\]\([^)]*\)/g, '')
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/^[#*>\s]+/gm, '')
      .replace(/\*\*|__|`/g, '').trim();
  }
  function handoff(value) {
    // Only an explicit final handoff is structured. Ordinary prose stays prose.
    const lines = [];
    let fence = '';
    for (const line of String(value || '').split('\n')) {
      const marker = /^\s*(`{3,}|~{3,})/.exec(line)?.[1];
      if (marker) { if (!fence) fence = marker; else if (marker[0] === fence[0] && marker.length >= fence.length) fence = ''; continue; }
      if (!fence) lines.push(line);
    }
    let start = -1;
    lines.forEach((line, index) => { if (/^#{1,3}\s+交接(?:说明)?\s*$/.test(line.trim())) start = index; });
    if (start < 0) return null;
    const result = {completed:'', attention:'', next:''};
    const names = {'已完成':'completed', '待确认':'attention', '下一步':'next'};
    const level = /^#+/.exec(lines[start].trim())[0].length;
    let section = '', found = false;
    for (const line of lines.slice(start + 1)) {
      const heading = /^(#{1,6})\s+(.+?)\s*$/.exec(line.trim());
      if (heading) {
        if (heading[1].length <= level) break;
        section = names[heading[2]] || '';
        found ||= !!section;
      } else if (section) result[section] += line + '\n';
    }
    if (!found) return null;
    for (const key of Object.keys(result)) result[key] = plain(result[key]);
    if (/^(?:[-•]\s*)?(?:无|暂无|无待确认事项|暂无待确认事项)[。.!！]?$/.test(result.attention)) result.attention = '';
    return result;
  }
  function isFinal(message) {
    const reason = String(message.stopReason || '').trim().toLowerCase();
    if (message.role !== 'assistant' || message.channel === 'commentary' || message.phase === 'commentary' || message.__openclawToolStreamLive) return false;
    for (const part of Array.isArray(message.content) ? message.content : []) {
      if (!['text','input_text','output_text'].includes(part.type) || typeof part.textSignature !== 'string') continue;
      try {
        const signature = JSON.parse(part.textSignature);
        if (signature.v === 1 && signature.phase === 'commentary') return false;
      } catch { /* Ordinary text signatures are not phase metadata. */ }
    }
    if (reason === 'error' || ((!reason || reason === 'tooluse') && message.__openclaw?.runTerminal !== true)) return false;
    const text = typeof message.content === 'string' ? message.content : (message.content || [])
      .filter(part => part.type === 'text').map(part => part.text).join('\n');
    return !!text.trim() && text.trim() !== 'NO_REPLY';
  }
  function createReplyTracker() {
    const sessions = new Map();
    return ({scope, turn, reply, completion, working, waiting, error, loading}) => {
      const terminal = completion?.phase === 'done' && completion.sessionKey && completion.runId ?
        `${completion.sessionKey}:${completion.runId}` : null;
      let seen = sessions.get(scope);
      if (!seen) {
        seen = {turn, baseline:reply?.key, terminal, delivered:null, text:null, pending:null, active:!!working};
        sessions.set(scope, seen);
        return null;
      }
      if (loading && !seen.active && !working) {
        // Hydration can start after the empty pane was already presented.
        seen.turn = turn; seen.baseline = reply?.key; seen.terminal = terminal;
        return null;
      }
      if (working || turn !== seen.turn) seen.active = true;
      if (turn !== seen.turn) {
        seen.turn = turn;
        if (!reply) seen.baseline = undefined;
        seen.delivered = null; seen.text = null; seen.pending = null;
      }
      if (error || completion?.phase === 'interrupted') {
        seen.baseline = reply?.key; seen.terminal = terminal; seen.pending = null; seen.active = false;
        return null;
      }
      if (terminal && terminal !== seen.terminal) seen.pending = terminal;
      const fresh = reply && reply.key !== seen.baseline;
      if (loading || working || waiting) return null;
      if (!fresh && !seen.pending && !seen.delivered) return null;
      const key = seen.delivered || seen.pending || `${scope}:${turn}:${reply.key}`;
      const text = fresh ? reply.text : seen.text ?? '';
      if (seen.delivered === key && seen.text === text) return null;
      seen.delivered = key; seen.text = text;
      seen.active = !text;
      if (reply) seen.baseline = reply.key;
      seen.terminal = terminal; seen.pending = null;
      return {key, text};
    };
  }
  return Object.freeze({workflows, outputs, compose, plain, handoff, isFinal, createReplyTracker, toolLabel});
});
