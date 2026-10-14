import React, { useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  ArrowRight,
  ArrowDown,
  Sun,
  Moon,
  Menu,
  X,
  Play,
  Check,
  Layers3,
  Clock3,
  Sparkles,
  MousePointer2,
  PanelRight,
  ShieldCheck,
  FileText,
  Command,
  Search,
  Plus,
  ChevronDown,
  Monitor,
  Circle,
  Link2,
} from "lucide-react";
import DayExperience from "./DayExperience.jsx";

const imageFiles = {
  day: "./images/hero-ai.webp",
  night: "./images/evening-ai.webp",
  vision: "./images/vision-ai.webp",
};

function Brand({ light = false, large = false }) {
  return (
    <span
      className={`brand ${light ? "brand-light" : ""} ${large ? "brand-large" : ""}`}
    >
      <img
        src={large ? "./mirally-large.png" : "./mirally.png"}
        alt=""
        width="44"
        height="44"
      />
      <span>
        mirally<span className="brand-period">.</span>
      </span>
    </span>
  );
}

function usePageMotion() {
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (media.matches || !("IntersectionObserver" in window)) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries)
          if (entry.isIntersecting) {
            entry.target.classList.add("in-view");
            observer.unobserve(entry.target);
          }
      },
      { threshold: 0.12 },
    );
    document
      .querySelectorAll("[data-reveal]")
      .forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, []);
}

function Header() {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState("");
  const progress = useRef(null);
  useEffect(() => {
    let frame;
    const onScroll = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const total = document.documentElement.scrollHeight - innerHeight;
        if (progress.current)
          progress.current.style.transform = `scaleX(${total > 0 ? scrollY / total : 0})`;
      });
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) setActive(entry.target.id);
        });
      },
      { rootMargin: "-20% 0px -55% 0px" },
    );
    ["a-day", "already-here", "vision"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("keydown", onKey);
    onScroll();
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("keydown", onKey);
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, []);
  return (
    <header className="site-header">
      <div className="header-inner">
        <a
          className="brand-link"
          href="#top"
          aria-label="Mirally 首页"
          onClick={() => setOpen(false)}
        >
          <Brand />
        </a>
        <nav
          className={open ? "main-nav nav-open" : "main-nav"}
          id="main-navigation"
          aria-label="主导航"
        >
          {[
            ["a-day", "与它的一天"],
            ["already-here", "已在发生"],
            ["vision", "我们的愿景"],
          ].map(([id, title]) => (
            <a
              href={`#${id}`}
              className={active === id ? "nav-active" : ""}
              key={id}
              onClick={() => setOpen(false)}
            >
              {title}
            </a>
          ))}
        </nav>
        <a className="header-cta" href="#a-day">
          遇见 Mirally <ArrowUpRight size={16} />
        </a>
        <button
          className="menu-toggle"
          aria-controls="main-navigation"
          aria-expanded={open}
          aria-label={open ? "关闭导航" : "打开导航"}
          onClick={() => setOpen(!open)}
        >
          {open ? <X /> : <Menu />}
        </button>
      </div>
      <div className="reading-progress" ref={progress} />
    </header>
  );
}

function Hero() {
  const [night, setNight] = useState(false);
  return (
    <section
      className={`hero ${night ? "hero-at-night" : ""}`}
      aria-labelledby="hero-title"
    >
      <div className="hero-halo" aria-hidden="true" />
      <div className="container hero-grid">
        <div className="hero-copy">
          <div className="eyebrow">
            <span className="status-dot" /> YOUR EVERYDAY ALLY
          </div>
          <h1 id="hero-title">
            工作时并肩，
            <br />
            <span>生活里相伴。</span>
          </h1>
          <p className="hero-lede">从一个任务，到你的每一天。</p>
          <p className="hero-description">
            我们正在打造一位理解当下、记得过往、
            <br className="desktop-break" />
            能够付诸行动的 AI 伙伴。你向前，它在身边。
          </p>
          <div className="hero-actions">
            <a className="button button-dark" href="#a-day">
              体验与 Mirally 的一天 <ArrowUpRight size={19} />
            </a>
            <a className="text-link" href="#vision">
              看见未来 <ArrowRight size={17} />
            </a>
          </div>
          <div className="hero-footnote">
            <span className="mini-line" /> 始于桌面，走向生活的更多可能。
          </div>
        </div>
        <div className="hero-art">
          <div className="hero-image-wrap">
            <img
              className="hero-image"
              src={night ? imageFiles.night : imageFiles.day}
              alt={
                night
                  ? "蓝调夜晚，温暖的客厅与放松时光"
                  : "自然光洒进温暖的生活与工作空间"
              }
              fetchPriority="high"
              width="1536"
              height="1024"
            />
            <div className="hero-photo-shade" />
            <div className="hero-scene-label">
              <span className="scene-label-dot" />
              {night
                ? "21:30 / TIME FOR YOURSELF"
                : "09:30 / A LITTLE MORE FOCUS"}
            </div>
            <div className="hero-vertical-note">
              A LITTLE CLOSER. EVERY DAY.
            </div>
            <div className="hero-time" aria-hidden="true">
              {night ? "21:30" : "09:30"}
              <span>
                {night ? "把时间，留给喜欢。" : "新的一天，一起开始。"}
              </span>
            </div>
          </div>
          <div
            className="hero-conversation"
            key={night ? "night" : "day"}
            aria-live="polite"
          >
            <div className="conversation-top">
              <div className="conversation-brand">
                <img src="./mirally.png" width="30" height="30" alt="" />
                <strong>Mirally</strong>
                <span>在你身边</span>
              </div>
              <span className="demo-label">愿景片段</span>
            </div>
            <div className="hero-question">
              {night
                ? "这个角色是谁？我好像见过他。"
                : "把刚才的灵感，整理成一个计划吧。"}
            </div>
            <div className="hero-answer">
              {night
                ? "一起看看他的故事。放心，只聊到你看过的那一集。"
                : "好，我们从最想实现的那件事开始。"}
              <span className="answer-spark">
                <Sparkles size={15} />
              </span>
            </div>
            <a href="#a-day" className="hero-conversation-link">
              试试这份默契 <ArrowRight size={14} />
            </a>
          </div>
          <div className="hero-switch" aria-label="切换首屏场景">
            <button
              className={!night ? "selected" : ""}
              aria-pressed={!night}
              onClick={() => setNight(false)}
            >
              <Sun size={15} /> 日间
            </button>
            <button
              className={night ? "selected" : ""}
              aria-pressed={night}
              onClick={() => setNight(true)}
            >
              <Moon size={15} /> 夜晚
            </button>
          </div>
          <span className="hero-art-caption">THE EVERYDAY, REIMAGINED.</span>
        </div>
      </div>
      <div className="container hero-bottom">
        <a href="#meaning">
          <ArrowDown size={16} /> 往下，看看生活的另一种可能
        </a>
        <span>01 — MEET YOUR ALLY</span>
      </div>
    </section>
  );
}

function Meaning() {
  return (
    <section
      className="meaning container"
      id="meaning"
      aria-labelledby="meaning-title"
    >
      <div className="meaning-word" data-reveal>
        <span className="eyebrow">THE WORD BEHIND THE NAME</span>
        <div>
          ally<span> /ˈælaɪ/</span>
        </div>
        <p>伙伴 · 同伴 · 与你站在一起的人</p>
      </div>
      <div className="meaning-copy" data-reveal>
        <h2 id="meaning-title">
          生活有很多面。
          <br />
          你可以有<span>同一个伙伴。</span>
        </h2>
        <p>
          在 Mirally 的名字里，Ally 就意味着伙伴。
          <br />
          我们希望这份默契，陪你从认真工作的时刻，走到自在生活的每一刻。
        </p>
      </div>
      <div className="meaning-bottom">
        <span>
          <Circle size={10} /> 理解此刻的情境
        </span>
        <span>
          <Link2 size={15} /> 接得上过往的思路
        </span>
        <span>
          <ArrowUpRight size={16} /> 把想法变成行动
        </span>
      </div>
    </section>
  );
}

function ProductIllustration() {
  return (
    <div
      className="product-illustration"
      aria-label="Mirally 侧边伙伴与工作空间的能力示意"
    >
      <div className="product-topbar">
        <div className="window-dots">
          <i />
          <i />
          <i />
        </div>
        <span>你的工作空间</span>
        <PanelRight size={14} />
      </div>
      <div className="product-body">
        <aside className="product-sidebar">
          <Command size={18} />
          <span className="selected">
            <FileText size={16} />
          </span>
          <Search size={16} />
          <Clock3 size={16} />
          <div />
          <Plus size={16} />
        </aside>
        <div className="product-document">
          <span className="document-breadcrumb">项目 / 下一次出发</span>
          <span className="document-icon">
            <FileText size={22} />
          </span>
          <h4>好想法，开始落地。</h4>
          <p>下一阶段 · 行动计划</p>
          <div className="document-rule" />
          <span className="document-task">
            <Check size={13} /> 整理讨论与关键结论
          </span>
          <span className="document-task">
            <Check size={13} /> 对齐优先级与负责人
          </span>
          <span className="document-task pending">
            <Circle size={12} /> 把下一步，交给行动
          </span>
          <div className="document-lines">
            <i />
            <i />
            <i />
          </div>
          <div className="document-tags">
            <span>思考</span>
            <span>协作</span>
            <span>行动</span>
          </div>
        </div>
        <div className="product-companion">
          <img src="./mirally.png" width="40" height="40" alt="" />
          <strong>一起，把事情向前推。</strong>
          <p>
            随时补充想法。
            <br />
            我会接着当前任务继续。
          </p>
          <div className="product-message">也把刚才提到的时间安排加进去。</div>
          <div className="product-delivery">
            <FileText size={18} />
            <div>
              行动计划.md<small>演示交付物</small>
            </div>
            <Check size={13} />
          </div>
          <span className="product-input">
            继续说说你的想法… <ArrowUpRight size={14} />
          </span>
        </div>
      </div>
      <span className="illustration-caption">产品能力示意</span>
    </div>
  );
}

const capabilities = [
  {
    icon: PanelRight,
    title: "随手就能找到的伙伴",
    text: "停在屏幕边缘，展开就能继续聊。会话、草稿和附件，与完整工作台自然衔接。",
    tag: "侧边伙伴",
  },
  {
    icon: MousePointer2,
    title: "从想法，走到交付",
    text: "在任务中补充要求，查看工具进展，接收文件成果；也能接入 Windows 桌面执行能力。",
    tag: "任务协作",
  },
  {
    icon: Layers3,
    title: "让经历，成为用得上的记忆",
    text: "查找活动历史，把片段整理为持久摘要。需要时，找回相关线索与上下文。",
    tag: "活动与记忆",
  },
];

function Foundations() {
  return (
    <section
      className="foundations"
      id="already-here"
      aria-labelledby="foundations-title"
    >
      <div className="container">
        <div className="section-heading" data-reveal>
          <div>
            <div className="eyebrow">
              <span className="status-dot" /> ALREADY TAKING SHAPE
            </div>
            <h2 id="foundations-title">
              未来的伙伴，
              <br />
              已经迈出第一步。
            </h2>
          </div>
          <p>
            从桌面出发，让对话、行动与记忆相连。
            <br />
            这些，正在成为 Mirally 的日常。
          </p>
        </div>
        <div className="foundation-grid">
          <div className="foundation-visual" data-reveal>
            <span className="pill pill-light">
              <span className="status-dot" /> 现有产品基础
            </span>
            <ProductIllustration />
            <div className="foundation-visual-bottom">
              <span>
                <Monitor size={16} /> 从 Windows 桌面开始
              </span>
              <span>CONTEXT → ACTION</span>
            </div>
          </div>
          <div className="capability-list">
            {capabilities.map(({ icon: Icon, title, text, tag }, i) => (
              <article key={title} className="capability" data-reveal>
                <div className="capability-number">0{i + 1}</div>
                <div>
                  <span className="capability-tag">
                    <Icon size={14} />
                    {tag}
                  </span>
                  <h3>{title}</h3>
                  <p>{text}</p>
                </div>
              </article>
            ))}
          </div>
        </div>
        <div className="trust-note">
          <ShieldCheck size={20} />
          <p>
            <strong>好的陪伴，也懂得分寸。</strong>
            <span>
              采集范围可以管理，原始记录在整理后按策略清理。重要操作保留你的决定权。
            </span>
          </p>
          <span className="trust-end">YOUR LIFE. YOUR CHOICE.</span>
        </div>
      </div>
    </section>
  );
}

const roadmap = [
  {
    step: "01",
    phase: "此刻",
    status: "正在构建",
    title: "一位桌面伙伴",
    text: "让对话、活动记忆和任务执行形成协作，先在每天的实际工作中帮上忙。",
    tags: ["侧边协作", "活动记忆", "工具与文件"],
  },
  {
    step: "02",
    phase: "下一步",
    status: "体验演进",
    title: "一份跨场景默契",
    text: "把画面、声音与长期上下文连接起来，从工作延伸到学习和娱乐。",
    tags: ["多模态交互", "场景理解", "连续协作"],
  },
  {
    step: "03",
    phase: "更远处",
    status: "长期愿景",
    title: "属于你的个人 AI",
    text: "跨越设备，理解长期目标，在你允许的范围内连接服务、主动提供帮助。",
    tags: ["跨设备陪伴", "长期目标", "服务生态"],
  },
];

function Vision() {
  return (
    <section className="vision" id="vision" aria-labelledby="vision-title">
      <div className="vision-opening">
        <div className="vision-image">
          <img
            src={imageFiles.vision}
            alt="晨光中的海面与远方的地平线"
            loading="lazy"
            width="2000"
            height="1333"
          />
        </div>
        <div className="vision-shade" />
        <div className="container vision-manifesto" data-reveal>
          <span className="eyebrow">OUR NORTH STAR</span>
          <h2 id="vision-title">
            让智能，
            <br />
            跟得上生活。
          </h2>
          <p>
            一个人的生活是连续的。
            <br />
            上午未完成的思考，下午偶然出现的灵感，
            <br />
            晚上随口问出的好奇，都属于同一个人。
          </p>
          <p>
            我们希望 Mirally 理解这份连续。
            <br />
            随着相处，更懂得怎样帮上你的忙。
          </p>
          <span className="vision-signature">
            A life in motion. An ally alongside.
          </span>
        </div>
        <div className="vision-orbit" aria-hidden="true">
          <div />
          <img src="./mirally-large.png" alt="" width="170" height="170" />
          <span>EVERYDAY, TOGETHER.</span>
        </div>
      </div>
      <div className="vision-roadmap container">
        <div className="roadmap-intro">
          <span className="eyebrow">THE ROAD AHEAD</span>
          <p>从一个任务，到你的每一天。</p>
        </div>
        <div className="roadmap-grid">
          {roadmap.map((item) => (
            <article className="roadmap-item" key={item.step} data-reveal>
              <div className="roadmap-step">
                <span>{item.step}</span>
                <div className="roadmap-line" />
                <small>{item.status}</small>
              </div>
              <span className="roadmap-phase">{item.phase}</span>
              <h3>{item.title}</h3>
              <p>{item.text}</p>
              <div className="roadmap-tags">
                {item.tags.map((tag) => (
                  <span key={tag}>{tag}</span>
                ))}
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

const businessPaths = [
  {
    number: "01",
    title: "个人订阅",
    subtitle: "从每天帮得上忙开始",
    content:
      "围绕高频工作与生活场景，提供更完整的模型、记忆与执行能力。用持续可感知的帮助，验证长期付费价值。",
    tags: "日常使用 · 持续价值",
  },
  {
    number: "02",
    title: "专业与团队",
    subtitle: "把默契带进更复杂的工作",
    content:
      "面向专业用户与团队，探索工作流程、共享知识与协作能力。让个人积累的经验，成为更高效协作的起点。",
    tags: "专业工作 · 团队协作",
  },
  {
    number: "03",
    title: "技能与服务生态",
    subtitle: "让需要，连接到合适的服务",
    content:
      "当个人 AI 能理解具体情境，就有机会连接第三方技能和服务。围绕用户授权，探索从发现需求到完成行动的服务网络。",
    tags: "开放能力 · 服务连接",
  },
];

function Opportunity() {
  return (
    <section
      className="opportunity container"
      aria-labelledby="opportunity-title"
    >
      <div className="opportunity-copy" data-reveal>
        <span className="eyebrow">A BIGGER POSSIBILITY</span>
        <h2 id="opportunity-title">
          成为个人 AI 的<br />
          <span>日常入口。</span>
        </h2>
        <p>
          更广的场景，带来更多相处的时刻。
          <br />
          持续的上下文，让每一次帮助更有意义。
          <br />
          真正的行动，让价值落在生活里。
        </p>
        <div className="growth-loop">
          <span>日常使用</span>
          <ArrowRight size={16} />
          <span>持续理解</span>
          <ArrowRight size={16} />
          <span>长期价值</span>
        </div>
        <span className="opportunity-caption">
          商业路径展望 · 随产品验证逐步推进
        </span>
      </div>
      <div className="business-paths">
        {businessPaths.map((item, index) => (
          <details
            className="business-path"
            key={item.number}
            open={index === 0 ? true : undefined}
            name="business-paths"
          >
            <summary>
              <span className="path-number">{item.number}</span>
              <span>
                <strong>{item.title}</strong>
                <small>{item.subtitle}</small>
              </span>
              <Plus className="path-plus" size={20} />
            </summary>
            <div className="path-content">
              <p>{item.content}</p>
              <span>{item.tags}</span>
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}

function Closing() {
  return (
    <section className="closing" aria-labelledby="closing-title">
      <div className="closing-glow" aria-hidden="true" />
      <div className="container closing-inner" data-reveal>
        <img
          className="closing-mark"
          src="./mirally-large.png"
          width="96"
          height="96"
          alt="Mirally 相伴 M 标识"
          loading="lazy"
        />
        <span className="eyebrow">YOUR EVERYDAY ALLY</span>
        <h2 id="closing-title">
          你的每一天，
          <br />
          值得一个好伙伴。
        </h2>
        <p>
          我们期待的未来，是每个人都有一份长期相伴的智能。
          <br />
          这段旅程，从今天开始。
        </p>
        <a className="button button-dark" href="#a-day">
          和 Mirally，开始一天 <ArrowUpRight size={19} />
        </a>
        <a className="back-to-top" href="#top">
          回到故事起点 <ArrowUpRight size={14} />
        </a>
      </div>
      <div className="closing-wordmark" aria-hidden="true">
        mirally
      </div>
    </section>
  );
}

function Footer() {
  const [creditsOpen, setCreditsOpen] = useState(false);
  const closeRef = useRef(null);
  const triggerRef = useRef(null);
  const dialogRef = useRef(null);
  useEffect(() => {
    if (creditsOpen) {
      dialogRef.current?.showModal();
      closeRef.current?.focus();
    } else if (dialogRef.current?.open) dialogRef.current.close();
  }, [creditsOpen]);
  const close = () => {
    setCreditsOpen(false);
    triggerRef.current?.focus();
  };
  return (
    <>
      <footer className="site-footer">
        <div className="container footer-inner">
          <a href="#top" aria-label="返回 Mirally 首页">
            <Brand />
          </a>
          <p>Everyday Ally. 每一天，都在身边。</p>
          <div>
            <span>© {new Date().getFullYear()} Mirally</span>
            <button ref={triggerRef} onClick={() => setCreditsOpen(true)}>
              关于这份展示 <ArrowUpRight size={13} />
            </button>
          </div>
        </div>
      </footer>
      <dialog
        className="credits-dialog"
        ref={dialogRef}
        onCancel={(e) => {
          e.preventDefault();
          close();
        }}
        onClick={(e) => {
          if (e.target === e.currentTarget) close();
        }}
        aria-labelledby="credits-title"
      >
        <button
          className="dialog-close"
          aria-label="关闭展示说明"
          ref={closeRef}
          onClick={close}
        >
          <X size={20} />
        </button>
        <span className="eyebrow">ABOUT THIS SHOWCASE</span>
        <h2 id="credits-title">一份关于未来的邀请。</h2>
        <p>
          这是 Mirally
          的产品与愿景展示。场景交互使用预设演示资料，未来体验不代表当前全部可用能力；不会访问你的活动记录、桌面或模型服务。
        </p>
        <p>
          现有能力依据项目文档整理。演员、角色与剧集均为虚构，下载的会议摘要也使用演示数据。
        </p>
        <div className="credits-assets">
          <strong>视觉与品牌</strong>
          <p>
            相伴 M 标识沿用 Mirally 品牌资产。宣传图由 AI
            生成，人物与情境均为虚构；完整生成记录与提示词随项目保存。
          </p>
          <a href="./CREDITS.md" target="_blank" rel="noreferrer">
            查看素材来源 <ArrowUpRight size={14} />
          </a>
        </div>
        <button className="button button-dark" onClick={close}>
          继续探索 <ArrowRight size={16} />
        </button>
      </dialog>
    </>
  );
}

export default function App() {
  usePageMotion();
  return (
    <>
      <a className="skip-link" href="#main">
        跳到主要内容
      </a>
      <div id="top" />
      <Header />
      <main id="main">
        <Hero />
        <Meaning />
        <DayExperience />
        <Foundations />
        <Vision />
        <Opportunity />
        <Closing />
      </main>
      <Footer />
    </>
  );
}
