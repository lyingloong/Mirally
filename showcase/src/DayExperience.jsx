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
    kicker: "周末市集 · 筹备会议",
    title: (
      <>
        会开完了，
        <br />
        谁来做下一步？
      </>
    ),
    description:
      "周末要办一场咖啡市集。场地、摊位、宣传都谈到了，让 Mirally 整理会议笔记，列出负责人和截止时间。",
    detail: "对话协作 · 任务推进 · 文件产出",
    questions: ["把这次会议整理成行动清单", "给我一份能直接用的会议摘要"],
    intro: "市集筹备会议结束了。要整理行动项，还是导出摘要？",
    answers: [
      "三项待办：小林周二确认场地，阿哲周四收齐摊位资料，小陈周五发布宣传。雨天方案还需要确认。",
      "摘要包含市集安排、三项待办和雨天预案问题，可以下载后发给同事。",
    ],
  },
  {
    id: "memory",
    time: "15:00",
    label: "接上思路",
    kicker: "周末市集 · 找回讨论",
    title: (
      <>
        上午怎么定的，
        <br />
        下午还能找到。
      </>
    ),
    description:
      "继续做市集海报，却忘了上午为什么选河边广场。问一句，找回相关记录，也能查看答案来自哪里。",
    detail: "活动记录 · 记忆整理 · 历史回顾",
    questions: ["上午定的市集方案是什么？", "为什么选河边广场？"],
    intro: "上午的筹备记录已整理。你想找回哪一段？",
    answers: [
      "周六下午在河边广场办咖啡市集，计划安排 12 个摊位。小林确认场地，阿哲收齐摊位资料，小陈负责宣传。",
      "筹备记录里写着：广场靠近地铁，附近有周末散步的人群。团队想先办一场小市集，看看大家是否愿意停下来喝杯咖啡。",
    ],
  },
  {
    id: "life",
    time: "21:30",
    label: "尽兴生活",
    kicker: "下班以后 · 看剧",
    title: (
      <>
        “这个人是谁？”
        <br />
        看剧时也能问。
      </>
    ),
    description:
      "晚上打开一部新剧，突然认不出画面里的角色。我们想让 Mirally 看懂你正在看的内容，回答当下的问题，并尊重你的观看进度。",
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
    <div className="scene-work-surface">
      <aside className="scene-doc-sidebar" aria-hidden="true">
        <span className="scene-sidebar-title">
          <FolderOpen size={16} /> 我的工作空间
        </span>
        <span className="scene-sidebar-item scene-sidebar-active">
          <FileText size={14} /> 咖啡市集筹备
        </span>
        <span className="scene-sidebar-item">
          <FileText size={14} /> 本周计划
        </span>
        <span className="scene-sidebar-item">
          <FileText size={14} /> 灵感收集
        </span>
        <span className="scene-sidebar-bottom">周末市集 · 项目资料</span>
      </aside>
      <div className="scene-document">
        <span className="scene-document-meta">团队笔记 / 市集筹备</span>
        <h4>周六，河边见。</h4>
        <p className="scene-document-date">周一 · 09:00 — 09:25 · 演示会议</p>
        <div className="scene-document-rule" />
        <h5>我们达成的共识</h5>
        <p>周六 14:00，在河边广场举办咖啡市集，计划安排 12 个摊位。</p>
        <h5>接下来，做三件事</h5>
        {["小林：确认场地", "阿哲：收齐摊位资料", "小陈：发布宣传"].map(
          (item, i) => (
            <div className="scene-task" key={item}>
              <span className="scene-task-check" aria-hidden="true" />
              <span>{item}</span>
              <small>{["周二", "周四", "周五"][i]} · 待办</small>
            </div>
          ),
        )}
        <div className="scene-document-note">
          <Sparkles size={14} />{" "}
          {answered
            ? "三项待办已整理；雨天预案待确认。"
            : "会议笔记 · 演示资料"}
        </div>
      </div>
    </div>
  );
}

function MemorySurface({ answered, sourceOpen, setSourceOpen }) {
  return (
    <div
      className={`scene-memory-surface ${sourceOpen ? "scene-memory-expanded" : ""}`}
    >
      <div className="scene-memory-heading">
        <span>YOUR DAY, CONNECTED</span>
        <h4>
          上午的讨论，
          <br />
          有记录可查。
        </h4>
        <p>同一个市集项目，继续往下做。</p>
      </div>
      <div className="scene-timeline">
        {[
          [
            "09:25",
            "咖啡市集筹备",
            "周六下午，河边广场，12 个摊位。",
            "会议笔记",
          ],
          ["10:10", "场地资料", "靠近地铁，周末散步的人群多。", "场地备选"],
          ["11:40", "宣传草稿", "海报主题：周六，河边见。", "海报文案"],
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
          <strong>09:25 · 咖啡市集筹备</strong>
          <p>
            河边广场靠近地铁，周末人流稳定。会议决定周六 14:00 开始，安排 12
            个摊位；雨天是否改期，周二确认场地时一起决定。
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
    <section className="day-section chapter" id="a-day">
      <div className="day-container">
        <header className="day-section-heading">
          <span className="day-eyebrow">02 / A DAY, WITH MIRALLY</span>
          <h2>和 Mirally 过一天。</h2>
          <p>上午推进工作，晚上放松一下。点击时间切换场景。</p>
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
              <span>点击右侧问题，体验一段对话</span>
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
                    : "剧集播放"}
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
