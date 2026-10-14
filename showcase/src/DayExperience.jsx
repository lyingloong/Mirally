import { useEffect, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowUpRight,
  ChevronDown,
  Clock3,
  FileText,
  FolderOpen,
  MessageCircle,
  Play,
  RotateCcw,
  Sparkles,
  X,
} from "lucide-react";
import "./day-experience.css";

const scenes = [
  {
    id: "work",
    time: "09:30",
    label: "专注工作",
    kicker: "一起，把事情向前推进。",
    title: (
      <>
        留一点时间，
        <br />
        给真正重要的事。
      </>
    ),
    description:
      "会议结束，思路还在继续。让 Mirally 帮你理清讨论、留下重点，把下一步变得清晰。你负责判断，它和你一起推进。",
    detail: "对话协作 · 任务推进 · 文件产出",
    questions: ["把这次会议整理成行动清单", "给我一份能直接用的会议摘要"],
    intro: "这次讨论的信息有点多。要一起理一理吗？",
    answers: [
      "整理好了：先明确体验目标，再完成关键场景原型，最后安排一次内部体验。每一步都有负责人和时间。",
      "摘要已准备好，包含讨论结论、行动项和待确认问题。你可以下载 Markdown，接着补充或分享。",
    ],
  },
  {
    id: "memory",
    time: "15:00",
    label: "接上思路",
    kicker: "让灵感，有迹可循。",
    title: (
      <>
        一句“刚才那个”，
        <br />
        也能接着聊。
      </>
    ),
    description:
      "上午读过的资料、讨论过的方向、突然闪过的念头。那些值得留下的片段，可以整理成记忆，在需要时重新接上。",
    detail: "活动记录 · 记忆整理 · 历史回顾",
    questions: ["上午说的那个体验方案是什么？", "为什么当时决定先做桌面端？"],
    intro: "有些思路，隔几个小时也值得继续。",
    answers: [
      "你们上午讨论的是「和 Mirally 的一天」：用工作、记忆、娱乐三个场景讲清楚日常伙伴的价值。这里是演示资料中的相关片段。",
      "演示会议记录里提到：桌面承载了高频工作情境，也适合验证“理解上下文 → 付诸行动”的完整体验。相关来源可以展开查看。",
    ],
  },
  {
    id: "life",
    time: "21:30",
    label: "尽兴生活",
    kicker: "每一个好奇，都有人回应。",
    title: (
      <>
        故事正精彩，
        <br />
        好奇也不用暂停。
      </>
    ),
    description:
      "窝进沙发，打开喜欢的剧集。认不出的角色、想了解的背景，随口一问就能继续看下去。我们期待这样的陪伴，也能发生在生活里。",
    detail: "未来愿景 · 画面理解 · 即时陪伴",
    questions: ["这个角色是谁？", "他和女主是什么关系？别剧透"],
    intro: "今晚，我们只聊到你看到的第三集。",
    answers: [
      "他是《潮汐之间》里的许舟，由虚构演员林序饰演。到第三集为止，他是一名回到海边故乡的摄影师。放心，没有后续剧透。",
      "截至第三集，许舟和陈澄是多年未见的旧友，正在一起寻找一卷遗失的胶片。后面的故事，就留给你慢慢看。",
    ],
  },
];

function downloadSummary() {
  const content =
    "# Mirally 产品体验讨论\n\n> 演示数据：此文档由展示页本地生成，并非真实会议记录。\n\n## 讨论结论\n\n- 以 Everyday Ally（日常伙伴）作为产品定位。\n- 用工作、记忆、娱乐三个场景呈现「和 Mirally 的一天」。\n- 先完成桌面体验验证，再探索更广泛的生活场景。\n\n## 行动清单\n\n| 事项 | 负责人（虚构） | 计划 |\n| --- | --- | --- |\n| 明确核心体验与演示脚本 | 产品组 | 周二 |\n| 完成三个场景的交互原型 | 设计组 | 周四 |\n| 组织内部体验与反馈 | 项目组 | 周五 |\n\n## 待确认\n\n- 哪些场景最能形成持续使用习惯？\n- 用户如何查看、纠正和删除记忆？\n";
  const url = URL.createObjectURL(
    new Blob([content], { type: "text/markdown;charset=utf-8" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "Mirally-会议摘要-演示.md";
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function WorkSurface({ answered }) {
  return (
    <div className="scene-work-surface">
      <aside className="scene-doc-sidebar" aria-hidden="true">
        <span className="scene-sidebar-title">
          <FolderOpen size={16} /> 我的工作空间
        </span>
        <span className="scene-sidebar-item scene-sidebar-active">
          <FileText size={14} /> 产品体验讨论
        </span>
        <span className="scene-sidebar-item">
          <FileText size={14} /> 本周计划
        </span>
        <span className="scene-sidebar-item">
          <FileText size={14} /> 灵感收集
        </span>
        <span className="scene-sidebar-bottom">一个想法，向前一步。</span>
      </aside>
      <div className="scene-document">
        <span className="scene-document-meta">团队笔记 / 体验讨论</span>
        <h4>让伙伴，走进日常。</h4>
        <p className="scene-document-date">周一 · 09:00 — 09:25 · 演示会议</p>
        <div className="scene-document-rule" />
        <h5>我们达成的共识</h5>
        <p>从熟悉的工作情境出发，让理解、记忆与行动连成一个自然的体验。</p>
        <h5>接下来，做三件事</h5>
        {[
          "明确核心体验与场景脚本",
          "完成三个场景的交互原型",
          "邀请团队一起体验",
        ].map((item, i) => (
          <div className="scene-task" key={item}>
            <span className="scene-task-check" aria-hidden="true" />
            <span>{item}</span>
            <small>{["周二", "周四", "周五"][i]} · 待办</small>
          </div>
        ))}
        <div className="scene-document-note">
          <Sparkles size={14} />{" "}
          {answered
            ? "行动清单已整理，等待一起推进。"
            : "留下结论，也留下下一步。"}
        </div>
      </div>
    </div>
  );
}

function MemorySurface({ answered, sourceOpen, setSourceOpen }) {
  return (
    <div className="scene-memory-surface">
      <div className="scene-memory-heading">
        <span>YOUR DAY, CONNECTED</span>
        <h4>
          今天的思路，
          <br />
          还在这里。
        </h4>
        <p>把片段留下，把线索接起来。</p>
      </div>
      <div className="scene-timeline">
        {[
          [
            "09:25",
            "产品体验讨论",
            "从桌面出发，探索日常伙伴的完整体验。",
            "会议笔记",
          ],
          [
            "10:10",
            "场景与叙事",
            "工作、记忆、娱乐，串起与 Mirally 的一天。",
            "方案草稿",
          ],
          [
            "11:40",
            "一个值得继续的想法",
            "帮助应该出现在好奇发生的那一刻。",
            "灵感笔记",
          ],
        ].map(([time, title, text, tag], index) => (
          <div
            className={`scene-timeline-item ${answered && index === 0 ? "scene-timeline-found" : ""}`}
            key={time}
          >
            <div className="scene-timeline-time">{time}</div>
            <div className="scene-timeline-dot" />
            <div className="scene-timeline-content">
              <small>{tag}</small>
              <h5>{title}</h5>
              <p>{text}</p>
            </div>
          </div>
        ))}
      </div>
      <button
        className="scene-source-toggle"
        onClick={() => setSourceOpen(!sourceOpen)}
        aria-expanded={sourceOpen}
        aria-controls="scene-source-detail"
      >
        <FileText size={14} />
        <span>查看来源摘要 · 演示资料</span>
        <ChevronDown
          size={14}
          className={sourceOpen ? "scene-chevron-open" : ""}
        />
      </button>
      {sourceOpen && (
        <div id="scene-source-detail" className="scene-source-detail">
          <strong>09:25 · 产品体验讨论</strong>
          <p>
            团队选择桌面作为起点：聚焦高频任务，验证上下文理解、可回顾记忆和任务执行。展示方案采用「和
            Mirally 的一天」，连接工作、学习与娱乐。
          </p>
          <small>本段为页面内预置的演示资料。</small>
        </div>
      )}
    </div>
  );
}

function LifeSurface({ answered, question }) {
  return (
    <div className="scene-life-surface">
      <img
        src="./images/story-ai.webp"
        alt="虚构剧集剧照：摄影师许舟穿着卡其外套，站在蓝调傍晚的海边码头"
        className="scene-coast"
      />
      <div className="scene-film-shade" />
      <div className="scene-film-info">
        <span>一部关于重逢与记忆的虚构剧集</span>
        <h4>潮汐之间</h4>
        <p>第三集 · 故乡的来信</p>
      </div>
      {!answered && (
        <div className="scene-film-subtitle">
          “海风会替我们，记住那些夏天。”
        </div>
      )}
      <div className="scene-player-controls" aria-hidden="true">
        <Play size={13} fill="currentColor" />
        <span>18:42 / 42:00</span>
        <div className="scene-player-progress">
          <i />
        </div>
        <span>HD</span>
      </div>
      {answered && (
        <div className="scene-character-card">
          <span className="scene-character-avatar">许</span>
          <div>
            <small>
              {question === 0 ? "角色资料" : "无剧透关系"} · 第 1—3 集
            </small>
            <strong>
              {question === 0 ? "许舟 / 林序 饰" : "许舟 ↔ 陈澄"}
            </strong>
            <p>
              {question === 0
                ? "摄影师 · 回到故乡的旧友"
                : "多年未见的旧友 · 共同寻找旧胶片"}
            </p>
          </div>
          <span className="scene-fiction-tag">虚构</span>
        </div>
      )}
    </div>
  );
}

export default function DayExperience() {
  const [active, setActive] = useState(0);
  const [question, setQuestion] = useState(null);
  const [typing, setTyping] = useState(false);
  const [answered, setAnswered] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(true);
  const timer = useRef(null);
  const tabs = useRef([]);
  const replayButton = useRef(null);
  const reopenButton = useRef(null);
  const chatFocusPending = useRef(false);
  const scene = scenes[active];

  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    if (!chatFocusPending.current) return;
    chatFocusPending.current = false;
    (chatOpen ? replayButton.current : reopenButton.current)?.focus({
      preventScroll: true,
    });
  }, [chatOpen]);
  function toggleChat(open) {
    chatFocusPending.current = true;
    setChatOpen(open);
  }
  function reset() {
    clearTimeout(timer.current);
    setQuestion(null);
    setTyping(false);
    setAnswered(false);
    setSourceOpen(false);
    setChatOpen(true);
  }
  function selectScene(index) {
    reset();
    setActive(index);
  }
  function ask(index) {
    clearTimeout(timer.current);
    setQuestion(index);
    setTyping(true);
    setAnswered(false);
    timer.current = setTimeout(
      () => {
        setTyping(false);
        setAnswered(true);
      },
      window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 100 : 700,
    );
  }
  function tabKey(event, index) {
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % scenes.length;
    else if (event.key === "ArrowLeft")
      next = (index + scenes.length - 1) % scenes.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = scenes.length - 1;
    if (next !== undefined) {
      event.preventDefault();
      selectScene(next);
      tabs.current[next]?.focus();
    }
  }

  return (
    <section className="day-section" id="a-day">
      <div className="day-container">
        <header className="day-section-heading">
          <span className="day-eyebrow">A DAY, WITH MIRALLY</span>
          <h2>
            你的每一天，
            <br />
            都有默契。
          </h2>
          <p>
            重要的事，琐碎的事，突然想问的事。
            <br className="day-desktop-break" />
            有一个伙伴，跟得上你的生活。
          </p>
        </header>
        <div
          className="day-tabs"
          role="tablist"
          aria-label="体验和 Mirally 的一天"
        >
          {scenes.map((item, index) => (
            <button
              key={item.id}
              id={`day-tab-${item.id}`}
              ref={(el) => {
                tabs.current[index] = el;
              }}
              role="tab"
              aria-selected={active === index}
              aria-controls={`day-panel-${item.id}`}
              tabIndex={active === index ? 0 : -1}
              onClick={() => selectScene(index)}
              onKeyDown={(event) => tabKey(event, index)}
              className={`day-tab ${active === index ? "day-tab-selected" : ""}`}
            >
              <span className="day-tab-time">{item.time}</span>
              <span>{item.label}</span>
              <span className="day-tab-dot" />
            </button>
          ))}
        </div>
        <div
          className={`day-experience day-experience-${scene.id}`}
          role="tabpanel"
          id={`day-panel-${scene.id}`}
          aria-labelledby={`day-tab-${scene.id}`}
          tabIndex={0}
        >
          <div className="day-story">
            <span className="day-story-kicker">{scene.kicker}</span>
            <h3>{scene.title}</h3>
            <p>{scene.description}</p>
            <span className="day-story-detail">{scene.detail}</span>
            <div className="day-try-hint">
              <ArrowUpRight size={17} />
              <span>点选演示问题，试试这份默契</span>
            </div>
          </div>
          <div className={`scene-desktop scene-desktop-${scene.id}`}>
            <div className="scene-window-bar">
              <span className="scene-window-dots" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
              <span>
                {active === 0
                  ? "工作空间"
                  : active === 1
                    ? "我的一天"
                    : "今晚，慢一点"}
              </span>
              <span className="scene-window-time">
                <Clock3 size={12} />
                {scene.time}
              </span>
            </div>
            <div className="scene-stage">
              {active === 0 ? (
                <WorkSurface answered={answered} />
              ) : active === 1 ? (
                <MemorySurface
                  answered={answered}
                  sourceOpen={sourceOpen}
                  setSourceOpen={setSourceOpen}
                />
              ) : (
                <LifeSurface answered={answered} question={question} />
              )}
              {chatOpen ? (
                <div className="scene-chat">
                  <div className="scene-chat-top">
                    <span className="scene-ally-icon">
                      <Sparkles size={16} />
                    </span>
                    <div>
                      <strong>Mirally</strong>
                      <small>Your Everyday Ally</small>
                    </div>
                    <button
                      ref={replayButton}
                      className="scene-icon-button"
                      onClick={reset}
                      aria-label="重新播放当前演示"
                      title="重新播放"
                    >
                      <RotateCcw size={14} />
                    </button>
                    <button
                      className="scene-icon-button"
                      onClick={() => toggleChat(false)}
                      aria-label="收起对话演示"
                      title="收起"
                    >
                      <X size={15} />
                    </button>
                  </div>
                  <div className="scene-chat-body">
                    {question !== null && (
                      <div className="scene-chat-user">
                        {scene.questions[question]}
                      </div>
                    )}
                    <div
                      className="scene-chat-answer"
                      aria-live="polite"
                      aria-atomic="true"
                    >
                      {typing ? (
                        <span className="scene-typing">
                          <i />
                          <i />
                          <i />
                          <span className="scene-sr-only">
                            Mirally 正在整理回答
                          </span>
                        </span>
                      ) : (
                        <p>
                          {answered ? scene.answers[question] : scene.intro}
                        </p>
                      )}
                    </div>
                    {answered && active === 0 && (
                      <button
                        onClick={downloadSummary}
                        className="scene-download"
                      >
                        <FileText size={15} />
                        <span>会议摘要 · 演示.md</span>
                        <ArrowDownToLine size={15} />
                      </button>
                    )}
                    {answered && active === 1 && (
                      <button
                        className="scene-chat-source"
                        onClick={() => setSourceOpen(!sourceOpen)}
                        aria-expanded={sourceOpen}
                        aria-controls="scene-source-detail"
                      >
                        <FileText size={13} />
                        {sourceOpen ? "收起来源摘要" : "展开来源摘要"}
                        <ArrowUpRight size={13} />
                      </button>
                    )}
                  </div>
                  <div className="scene-question-list">
                    {scene.questions.map((text, index) => (
                      <button
                        key={text}
                        className={
                          question === index
                            ? "scene-question scene-question-selected"
                            : "scene-question"
                        }
                        onClick={() => ask(index)}
                      >
                        <span>{text}</span>
                        <ArrowUpRight size={13} />
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <button
                  ref={reopenButton}
                  className="scene-chat-reopen"
                  onClick={() => toggleChat(true)}
                >
                  <MessageCircle size={17} /> 和 Mirally 聊聊
                </button>
              )}
            </div>
            <div className="scene-demo-caption">
              <span>
                <i />
                交互概念演示
              </span>
              <span>
                {active === 2 ? "未来体验 · 虚构剧集" : "脚本互动 · 演示资料"}
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
