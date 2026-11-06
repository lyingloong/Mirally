import { useState } from "react";
import { ArrowRight, FileText, Image, MessageCircle, Play, Square, Volume2 } from "lucide-react";

export default function HeroExperience() {
  const [life, setLife] = useState(false);
  return (
    <div className={`hero-art hero-product-art ${life ? "hp-life" : ""}`}>
      <div className="hp-glow" aria-hidden="true" />
      <div className="hp-shell">
        <div className="hp-window-bar">
          <span className="hp-window-title">{life ? <Play size={14} /> : <FileText size={14} />}{life ? "看剧陪伴 · 构想" : "工作协作 · 示意"}</span>
          <div className="hp-switch" aria-label="切换首页体验">
            <button aria-pressed={!life} onClick={() => setLife(false)}>工作</button>
            <button aria-pressed={life} onClick={() => setLife(true)}>生活</button>
          </div>
        </div>
        <div className="hp-scene">
          {life ? (
            <div className="hp-film">
              <img src="./images/story-ai.webp" alt="虚构剧集画面：摄影师许舟站在海边码头" width="1536" height="1024" />
              <span className="hp-episode">第 03 集</span>
              <div className="hp-player" aria-hidden="true"><div className="hp-player-track"><i /></div><span><Play size={13} fill="currentColor" />18:24<Volume2 size={14} /></span></div>
            </div>
          ) : (
            <div className="hp-work">
              <span className="hp-document-label">活动方案</span>
              <h3>周末市集</h3>
              <img className="hp-market" src="./images/hero-market-detail.webp" alt="筹备资料中的河边市集：咖啡摊位与人行步道" width="697" height="394" fetchPriority="high" />
              <p className="hp-work-caption">周六下午 · 河边广场</p>
              <div className="hp-materials"><span><Image size={14} />场地参考</span><span><FileText size={14} />会议笔记</span></div>
            </div>
          )}
          <div className="hp-ally" aria-live="polite">
            <div className="hp-ally-heading"><img src="./mirally.png" alt="" width="28" height="28" /><strong>Mirally</strong><span>概念演示</span></div>
            <div className="hp-question">{life ? "这个角色是谁？" : "把会议整理成待办。"}</div>
            {life ? (
              <div className="hp-character"><span className="hp-answer-label">画面中的人物</span><strong>许舟</strong><span>摄影师 · 林序 饰</span><p>只聊到第三集，不剧透。</p></div>
            ) : (
              <div className="hp-tasks"><span className="hp-answer-label">待办已整理</span><span><Square />确认摊位</span><span><Square />完成海报</span><div className="hp-file"><FileText size={19} /><span>会议摘要.md</span></div></div>
            )}
            <a className="hp-open" href="#a-day">查看场景演示<ArrowRight size={14} /></a>
            <div className="hp-input" aria-hidden="true"><span>继续聊…</span><MessageCircle size={14} /></div>
          </div>
        </div>
      </div>
    </div>
  );
}
