import React, { useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  ArrowRight,
  ArrowDown,
  Sun,
  Moon,
  Menu,
  X,
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
  Monitor,
  Circle,
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
    ["top", "a-day", "already-here", "vision", "opportunity"].forEach((id) => {
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
            ["already-here", "当前能力"],
            ["vision", "我们的愿景"],
            ["opportunity", "商业路径"],
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
      <nav className="chapter-nav" aria-label="章节导航">
        {[
          ["top", "认识 Mirally"],
          ["a-day", "与它的一天"],
          ["already-here", "当前能力"],
          ["vision", "我们的愿景"],
          ["opportunity", "商业路径"],
        ].map(([id, label], index) => (
          <a
            key={id}
            href={`#${id}`}
            aria-label={`${index + 1}. ${label}`}
            aria-current={active === id ? "location" : undefined}
          >
            <span>{label}</span>
            <i />
          </a>
        ))}
      </nav>
    </header>
  );
}

function Hero() {
  const [night, setNight] = useState(false);
  return (
    <section
      className={`hero chapter ${night ? "hero-at-night" : ""}`}
      id="top"
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
          <p className="hero-lede">Mirally，你的 Everyday Ally。</p>
          <p className="hero-description">
            Ally，就是伙伴。我们想让 Mirally 陪你完成手头的工作，
            也陪你找回一个想法，或聊聊正在看的剧。
          </p>
          <div className="hero-actions">
            <a className="button button-dark" href="#a-day">
              体验与 Mirally 的一天 <ArrowUpRight size={19} />
            </a>
            <a className="text-link" href="#vision">
              我们的愿景 <ArrowRight size={17} />
            </a>
          </div>
          <div className="hero-footnote">
            <span className="mini-line" /> 从 Windows 桌面开始。
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
                : "把刚才讨论的事整理一下，列出下一步。"}
            </div>
            <div className="hero-answer">
              {night
                ? "一起看看他的故事。放心，只聊到你看过的那一集。"
                : "摘要和待办清单整理好了。时间安排还需要你确认。"}
              <span className="answer-spark">
                <Sparkles size={15} />
              </span>
            </div>
            <a href="#a-day" className="hero-conversation-link">
              打开场景演示 <ArrowRight size={14} />
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
        <a href="#a-day">
          <ArrowDown size={16} /> 接下来，看看它怎么参与一天
        </a>
        <span>01 — MEET YOUR ALLY</span>
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
          <span className="document-breadcrumb">项目 / 周末咖啡市集</span>
          <span className="document-icon">
            <FileText size={22} />
          </span>
          <h4>周末活动安排</h4>
          <p>周六 14:00 · 河边广场</p>
          <div className="document-rule" />
          <span className="document-task">
            <Check size={13} /> 整理讨论与关键结论
          </span>
          <span className="document-task">
            <Check size={13} /> 对齐优先级与负责人
          </span>
          <span className="document-task pending">
            <Circle size={12} /> 确认场地与人员安排
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
          <strong>活动计划已更新。</strong>
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
    title: "随时展开，继续对话",
    text: "从屏幕边缘展开侧边伙伴，会话、草稿和附件与完整工作台同步。",
    tag: "侧边伙伴",
  },
  {
    icon: MousePointer2,
    title: "交付文件，也能执行任务",
    text: "中途补充要求，查看工具进展，拿到文件成果。需要时可接入 Windows 桌面执行能力。",
    tag: "任务协作",
  },
  {
    icon: Layers3,
    title: "找回看过和做过的事",
    text: "活动记录会整理为持久摘要。想继续之前的事，可以从历史与记忆中查找相关线索。",
    tag: "活动与记忆",
  },
];

function Foundations() {
  return (
    <section
      className="foundations chapter"
      id="already-here"
      aria-labelledby="foundations-title"
    >
      <div className="container">
        <div className="section-heading" data-reveal>
          <div>
            <div className="eyebrow">
              <span className="status-dot" /> 03 / BUILT SO FAR
            </div>
            <h2 id="foundations-title">从桌面开始，已经能做这些。</h2>
          </div>
          <p>
            前面的场景有了怎样的基础？
            <br />
            当前版本已支持对话协作、文件产出和活动记忆。
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
            <strong>采集与操作，由你掌握。</strong>
            <span>
              采集范围可管理，原始记录在整理成功后按策略清理。重要操作由你确认。
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
    title: "做好桌面上的事",
    text: "把对话、活动记忆和任务执行用顺，先在实际工作中帮上忙。",
    tags: ["侧边协作", "活动记忆", "工具与文件"],
  },
  {
    step: "02",
    phase: "下一步",
    status: "体验演进",
    title: "看懂更多场景",
    text: "探索画面理解和连续语音交流，从工作延伸到学习与娱乐。",
    tags: ["多模态交互", "场景理解", "连续协作"],
  },
  {
    step: "03",
    phase: "更远处",
    status: "长期愿景",
    title: "跨设备的个人 AI",
    text: "在你的授权下记住长期目标，连接所需服务，主动帮忙。",
    tags: ["跨设备陪伴", "长期目标", "服务生态"],
  },
];

function Vision() {
  return (
    <section
      className="vision chapter"
      id="vision"
      aria-labelledby="vision-title"
    >
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
          <span className="eyebrow">04 / OUR NORTH STAR · 长期愿景</span>
          <h2 id="vision-title">
            每个人，都有
            <br />
            自己的 AI 伙伴。
          </h2>
          <p>
            我们想让它知道你正在做什么，也记得你为什么开始。
            换一台设备、进入一个新场景，你们可以接着聊、接着做。 向 Mirally
            求助，能像问身边的朋友一样自然。
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
          <p>从今天的基础，走向全天候的陪伴。</p>
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
    subtitle: "为持续的帮助付费",
    content:
      "围绕高频任务提供更完整的模型、记忆与执行能力，用实际节省的时间和交付质量验证订阅价值。",
    tags: "日常使用 · 持续价值",
  },
  {
    number: "02",
    title: "专业与团队",
    subtitle: "把个人效率带进协作",
    content:
      "从个人工作延伸到共享资料、团队流程和专业任务，探索按席位或能力付费。",
    tags: "专业工作 · 团队协作",
  },
  {
    number: "03",
    title: "技能与服务生态",
    subtitle: "从问一句，到办成一件事",
    content:
      "在用户授权下接入第三方技能和服务，探索服务分成等商业模式。具体情境里的需求，有机会直接带来交易。",
    tags: "开放能力 · 服务连接",
  },
];

function Opportunity() {
  return (
    <section
      className="opportunity chapter"
      id="opportunity"
      aria-labelledby="opportunity-title"
    >
      <div className="container opportunity-grid">
        <div className="opportunity-copy" data-reveal>
          <span className="eyebrow">05 / THE BUSINESS AHEAD</span>
          <h2 id="opportunity-title">
            成为人们每天
            <br />
            <span>都会打开的 AI。</span>
          </h2>
          <p>
            我们的判断是：能接着昨天的事继续帮忙，用户就有理由明天再打开它。
            先验证日常使用，再发展订阅、专业协作与服务连接。
          </p>
          <div className="growth-loop">
            <span>日常使用</span>
            <ArrowRight size={16} />
            <span>愿意留下</span>
            <ArrowRight size={16} />
            <span>持续付费</span>
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
      </div>
      <div className="container closing-strip">
        <p>
          <strong>Mirally · Your Everyday Ally</strong>
          <span>先成为每天帮得上忙的伙伴。</span>
        </p>
        <a className="text-link" href="#a-day">
          再体验一次 <ArrowUpRight size={17} />
        </a>
      </div>
      <Footer />
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
        <h2 id="credits-title">关于这份展示</h2>
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
      <Header />
      <main id="main">
        <Hero />
        <DayExperience />
        <Foundations />
        <Vision />
        <Opportunity />
      </main>
    </>
  );
}
