import React from "react";
import { ArrowUp, Check, FileText, Search, Square } from "lucide-react";

function ConversationPreview() {
  return (
    <div className="cv-chat-layout">
      <div className="cv-document">
        <span className="cv-document-label">会议笔记</span>
        <strong>周末市集</strong>
        <img src="./images/market-reference-thumb.webp" alt="" width="320" height="200" loading="lazy" />
        <div className="cv-document-detail"><span>场地</span>河边广场</div>
        <div className="cv-document-detail"><span>安排</span>确认摊位与海报</div>
      </div>
      <div className="cv-sidebar">
        <div className="cv-ally"><img src="./mirally.png" width="20" height="20" alt="" /><strong>Mirally</strong></div>
        <div className="cv-question">把笔记整理成待办</div>
        <div className="cv-reply">
          <span>整理好了</span>
          <div><Square />确认摊位</div>
          <div><Square />完成海报</div>
        </div>
        <div className="cv-compose"><span>继续聊…</span><ArrowUp /></div>
      </div>
    </div>
  );
}

function TaskPreview() {
  return (
    <div className="cv-task-layout">
      <div className="cv-task-progress">
        <span className="cv-task-label">整理会议资料</span>
        <div className="cv-steps">
          {["读取资料", "整理内容", "生成文件"].map((step) => <div className="cv-step" key={step}><span><Check /></span>{step}</div>)}
        </div>
      </div>
      <div className="cv-output">
        <div className="cv-paper">
          <FileText className="cv-paper-icon" />
          <strong>会议摘要</strong>
          <span className="cv-paper-subtitle">周末市集筹备</span>
          <div className="cv-paper-lines"><i /><i /><i /></div>
          <span className="cv-file-status"><Check />已生成</span>
        </div>
        <span className="cv-filename">会议摘要.md</span>
      </div>
    </div>
  );
}

function MemoryPreview() {
  return (
    <div className="cv-memory-layout">
      <div className="cv-search"><Search /><span>上午为什么选这个场地？</span></div>
      <div className="cv-timeline">
        <div className="cv-record cv-record-muted"><time>09:40</time><div><strong>市集筹备</strong><span>讨论场地与摊位</span></div></div>
        <div className="cv-record cv-record-match"><time>10:10</time><div><strong>场地选择</strong><span>靠近地铁，周末人流多。</span><small>来源：活动摘要</small></div></div>
      </div>
    </div>
  );
}

export default function CapabilityVisual({ kind, alt }) {
  const title = { chat: "工作文档", task: "任务执行", memory: "活动记忆" }[kind];
  return (
    <div className={`capability-visual capability-visual-${kind}`} role="img" aria-label={alt}>
      <div className="cv-window" aria-hidden="true">
        <div className="cv-titlebar"><span className="cv-window-mark" /><span>{title}</span><span className="cv-illustration-label">界面示意</span></div>
        {kind === "chat" ? <ConversationPreview /> : kind === "task" ? <TaskPreview /> : <MemoryPreview />}
      </div>
    </div>
  );
}
