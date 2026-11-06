import { useEffect, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowUpRight,
  ChevronDown,
  FileText,
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
    label: "工作",
    title: "会议整理与文件产出",
    questions: ["整理会议待办", "导出会议摘要"],
    intro: "选择一个问题，查看整理结果。",
    answers: [
      "已整理 3 项待办，负责人和截止时间见清单。",
      "会议摘要已生成，可以下载。",
    ],
  },
  {
    id: "memory",
    label: "记忆",
    title: "找回活动记录",
    questions: ["上午定了什么方案？", "为什么选河边广场？"],
    intro: "试着问问上午的记录。",
    answers: [
      "周六下午，河边广场，12 个摊位。来源：09:25 会议记录。",
      "靠近地铁，周末人流多。来源：10:10 场地资料。",
    ],
  },
  {
    id: "life",
    label: "生活",
    title: "画面问答",
    questions: ["这个角色是谁？", "他和女主什么关系？"],
    intro: "根据当前画面提问，回答只到第三集。",
    answers: [
      "许舟，摄影师，由林序饰演。",
      "许舟和陈澄是旧友。截至第三集，两人在寻找遗失的胶片。",
    ],
  },
];

function downloadSummary() {
  const content =
    "# 咖啡市集筹备会议\n\n> 演示数据：此文档由展示页本地生成，人物与活动均为虚构。\n\n## 讨论结论\n\n- 周六 14:00，在河边广场举办咖啡市集。\n- 计划安排 12 个摊位。\n- 选择河边广场是因为靠近地铁，周末散步人群多。\n\n## 行动清单\n\n| 事项 | 负责人（虚构） | 截止时间 | 状态 |\n| --- | --- | --- | --- |\n| 确认场地 | 小林 | 周二 | 待办 |\n| 收齐摊位资料 | 阿哲 | 周四 | 待办 |\n| 发布宣传 | 小陈 | 周五 | 待办 |\n\n## 待确认\n\n- 雨天是否改期？周二确认场地时一起决定。\n";
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
    <div
      className={`scene-work-surface ${answered ? "scene-work-organized" : ""}`}
    >
      <div className="scene-document">
        <h4>
          <FileText size={19} aria-hidden="true" />
          市集会议笔记
        </h4>
        <p>周六下午 · 河边广场 · 12 个摊位</p>
        {!answered && (
          <div className="scene-raw-notes">
            <p>
              小林周二前确认场地，阿哲周四收齐摊位资料。小陈负责宣传，周五前发布海报。
            </p>
            <p>还要确认：遇到下雨，市集是否改期？</p>
          </div>
        )}
        {answered && (
          <div className="scene-work-tasks" aria-label="整理后的行动清单">
            {["小林：确认场地", "阿哲：收齐摊位资料", "小陈：发布宣传"].map(
              (item, i) => (
                <div className="scene-task" key={item}>
                  <span className="scene-task-check" aria-hidden="true" />
                  <span>{item}</span>
                  <small>{["周二", "周四", "周五"][i]} · 待办</small>
                </div>
              ),
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function MemorySurface({ answered, question, sourceOpen, setSourceOpen }) {
  return (
    <div
      className={`scene-memory-surface ${answered ? "scene-memory-answered" : ""} ${sourceOpen ? "scene-memory-expanded" : ""}`}
    >
      <div className="scene-memory-heading">
        <h4>今天的活动记录</h4>
      </div>
      <div className="scene-timeline">
        {[
          ["09:25", "会议记录", "周六下午 · 河边广场 · 12 个摊位"],
          ["10:10", "场地资料", "靠近地铁 · 周末人流多"],
          ["11:40", "宣传草稿", "市集海报 · 第一版"],
        ].map(([time, title, text], index) => (
          <div
            className={`scene-timeline-item ${answered && index === question ? "scene-timeline-found" : ""} ${index === (answered ? question : 0) ? "scene-source-selected" : ""}`}
            key={time}
          >
            <div className="scene-timeline-time">{time}</div>
            <div className="scene-timeline-dot" />
            <div className="scene-timeline-content">
              <span
                className={`scene-record-preview scene-record-preview-${index}`}
                aria-hidden="true"
              >
                {index === 0 ? (
                  <span className="scene-record-paper">
                    <i />
                    <i />
                    <i />
                    <i />
                  </span>
                ) : (
                  <img
                    src={
                      index === 1
                        ? "./images/market-reference-thumb.webp"
                        : "./images/market-poster-thumb.webp"
                    }
                    alt=""
                  />
                )}
              </span>
              <div className="scene-record-copy">
                <h5>{title}</h5>
                <p>{text}</p>
              </div>
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
        <span>查看来源摘要</span>
        <ChevronDown
          size={14}
          className={sourceOpen ? "scene-chevron-open" : ""}
        />
      </button>
      {sourceOpen && (
        <div id="scene-source-detail" className="scene-source-detail">
          <strong>09:25 会议记录 / 10:10 场地资料</strong>
          <p>河边广场靠近地铁，周末人流多。周六下午开办，安排 12 个摊位。</p>
        </div>
      )}
    </div>
  );
}

function LifeSurface({ answered, question }) {
  return (
    <div className="scene-life-surface">
      <div className="scene-film">
        <img
          src="./images/story-ai.webp"
          alt="虚构剧集剧照：摄影师许舟穿着卡其外套，站在蓝调傍晚的海边码头"
          className="scene-coast"
        />
        <div className="scene-film-shade" />
        <div className="scene-player-controls" aria-hidden="true">
          <Play size={13} fill="currentColor" />
          <span>18:42 / 42:00</span>
          <div className="scene-player-progress">
            <i />
          </div>
          <span>HD</span>
        </div>
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
            <p>{question === 0 ? "摄影师" : "旧友 · 寻找遗失的胶片"}</p>
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
    <section className="day-section chapter" id="a-day">
      <div className="container day-container">
        <header className="day-section-heading">
          <p className="eyebrow">交互演示</p>
          <h2>从一句话，到下一步。</h2>
          <p className="day-demo-note">
            预设资料与回答，非产品实录；生活陪伴为未来概念。
          </p>
        </header>
        <div className="day-tabs" role="tablist" aria-label="Mirally 应用场景">
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
              <span>{item.label}</span>
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
          <div className={`scene-desktop scene-desktop-${scene.id}`}>
            <div className="scene-window-bar">
              <strong>{scene.title}</strong>
              <span>{active === 2 ? "概念演示" : "预设演示"}</span>
            </div>
            <div className="scene-stage">
              {active === 0 ? (
                <WorkSurface answered={answered} />
              ) : active === 1 ? (
                <MemorySurface
                  answered={answered}
                  question={question}
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
                  <MessageCircle size={17} /> 展开对话演示
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
