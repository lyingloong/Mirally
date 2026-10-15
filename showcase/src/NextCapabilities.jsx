import { BellRing, FileText, Mic, Sparkles } from "lucide-react";

function VoiceVisual() {
  return (
    <div className="next-visual next-visual-voice" role="img" aria-label="语音交流概念示意：对着文档说再简洁一点，Mirally 将长段落整理成三条要点。">
      <div className="next-voice-canvas" aria-hidden="true">
        <div className="next-document next-voice-document">
          <span className="next-document-heading"><FileText size={15} /> 周报草稿</span>
          <div className="next-draft-lines"><i /><i /><i /><i /></div>
          <div className="next-voice-result"><span>本周进展</span><i /><i /><i /></div>
        </div>
        <div className="next-voice-bubble"><Mic size={17} /><span>再简洁一点</span><div className="next-wave">{[9, 18, 27, 14, 22, 10, 17].map((height, index) => <i key={index} style={{ height }} />)}</div></div>
      </div>
    </div>
  );
}

function ProactiveVisual() {
  return (
    <div className="next-visual next-visual-proactive" role="img" aria-label="主动协助概念示意：日历安排了十五点汇报，Mirally 提前询问是否需要整理本周进展。">
      <div className="next-proactive-canvas" aria-hidden="true">
        <div className="next-calendar">
          <div className="next-calendar-heading"><span>周五</span><strong>15</strong></div>
          <div className="next-calendar-lines"><span>15:00</span><div className="next-meeting">本周工作汇报</div></div>
        </div>
        <div className="next-reminder"><div className="next-reminder-heading"><span><Sparkles size={15} /> Mirally</span><BellRing size={14} /></div><p>需要整理这周的进展吗？</p><span className="next-start">开始整理 <span>↗</span></span></div>
      </div>
    </div>
  );
}

function PersonalVisual() {
  return (
    <div className="next-visual next-visual-personal" role="img" aria-label="个性化协作概念示意：说按我常用的格式来，将会议记录、任务清单和数据整理成一页汇报。">
      <div className="next-personal-canvas" aria-hidden="true">
        <div className="next-preference"><Sparkles size={15} /> 按我常用的格式来</div>
        <div className="next-report-flow">
          <div className="next-file-stack"><span><FileText size={13} /> 会议记录</span><span><FileText size={13} /> 任务清单</span><span><FileText size={13} /> 本周数据</span></div>
          <span className="next-flow-arrow">→</span>
          <div className="next-document next-report"><span className="next-report-title">一页汇报</span><div className="next-report-metrics"><i /><i /><i /></div><div className="next-report-section"><b /> <i /><i /></div><div className="next-report-chart"><i /><i /><i /><i /><i /></div></div>
        </div>
      </div>
    </div>
  );
}

const nextCapabilities = [
  { title: "语音交流", text: "开口交代，也能随时补充。", icon: Mic, visual: VoiceVisual },
  { title: "主动协助", text: "结合安排与进展，提醒下一步。", icon: BellRing, visual: ProactiveVisual },
  { title: "个性化协作", text: "记住你的习惯，按你喜欢的方式完成工作。", icon: Sparkles, visual: PersonalVisual },
];

export default function NextCapabilities() {
  return (
    <section className="foundations chapter upcoming next-capabilities" id="next-up" aria-labelledby="next-capabilities-title">
      <div className="container">
        <div className="section-heading" data-reveal><h2 id="next-capabilities-title">下一步能力</h2><span className="feature-status">近期开发方向</span></div>
        <div className="capability-cards">
          {nextCapabilities.map(({ title, text, icon: Icon, visual: Visual }) => (
            <article className="capability-card" key={title} data-reveal><Visual /><div className="capability-copy"><h3><Icon size={23} />{title}</h3><p>{text}</p></div></article>
          ))}
        </div>
        <div className="next-concept-note">概念示意</div>
      </div>
    </section>
  );
}
