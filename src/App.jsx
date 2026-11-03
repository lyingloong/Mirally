import React, { useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  ArrowRight,
  ArrowDown,
  Menu,
  X,
  Layers3,
  MousePointer2,
  PanelRight,
  ShieldCheck,
  Monitor,
} from "lucide-react";
import DayExperience from "./DayExperience.jsx";
import CapabilityVisual from "./CapabilityVisual.jsx";
import NextCapabilities from "./NextCapabilities.jsx";
import HeroExperience from "./HeroExperience.jsx";
import BusinessVisual from "./BusinessVisual.jsx";

const chapters = [
  ["top", "认识 Mirally"],
  ["a-day", "场景演示"],
  ["already-here", "当前能力"],
  ["next-up", "下一步能力"],
  ["vision", "我们的愿景"],
  ["opportunity", "商业路径"],
];

const imageFiles = {
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
    chapters.forEach(([id]) => {
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
          {chapters.slice(1).map(([id, title]) => (
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
          查看演示 <ArrowUpRight size={16} />
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
        {chapters.map(([id, label], index) => (
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
  return (
    <section
      className="hero chapter"
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
          <p className="hero-lede">Ally，就是伙伴。</p>
          <p className="hero-description">
            陪你处理工作、找回记忆，也聊聊正在看的剧。
          </p>
          <div className="hero-actions">
            <a className="button button-dark" href="#a-day">
              查看场景演示 <ArrowUpRight size={19} />
            </a>
            <a className="text-link" href="#vision">
              我们的愿景 <ArrowRight size={17} />
            </a>
          </div>
        </div>
        <HeroExperience />
      </div>
      <div className="container hero-bottom">
        <a href="#a-day">
          <ArrowDown size={16} /> 查看功能演示
        </a>
      </div>
    </section>
  );
}

const capabilities = [
  {
    icon: PanelRight,
    title: "侧边对话",
    text: "随时对话、截图提问，会话同步。",
    kind: "chat",
    alt: "侧边对话界面示意：左侧是市集会议笔记，右侧 Mirally 将笔记整理成待办。",
  },
  {
    icon: MousePointer2,
    title: "任务执行",
    text: "生成文件，可接入桌面操作。",
    kind: "task",
    alt: "任务执行界面示意：读取资料、整理内容、生成会议摘要文件。",
  },
  {
    icon: Layers3,
    title: "活动记忆",
    text: "整理活动摘要，按问题找回相关记录。",
    kind: "memory",
    alt: "活动记忆界面示意：询问场地选择原因，时间线中找到上午十点十分的活动摘要。",
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
          <h2 id="foundations-title">当前能力</h2>
          <span className="feature-status">
            <Monitor size={17} /> Windows 桌面版
          </span>
        </div>
        <div className="capability-cards">
          {capabilities.map(({ icon: Icon, title, text, kind, alt }) => (
            <article key={kind} className="capability-card" data-reveal>
              <CapabilityVisual kind={kind} alt={alt} />
              <div className="capability-copy">
                <h3>
                  <Icon size={23} />
                  {title}
                </h3>
                <p>{text}</p>
              </div>
            </article>
          ))}
        </div>
        <div className="trust-note">
          <ShieldCheck size={23} />
          <p>
            <strong>采集可管理，重要操作需确认。</strong>
            <span>原始记录在整理成功后按策略清理。</span>
          </p>
        </div>
      </div>
    </section>
  );
}

const roadmap = [
  {
    step: "01",
    phase: "当前",
    title: "桌面协作",
    text: "对话、画面理解、任务与活动记忆。",
  },
  {
    step: "02",
    phase: "下一步",
    title: "自然交流与主动协作",
    text: "语音交流、主动协助与个人偏好。",
  },
  {
    step: "03",
    phase: "长期目标",
    title: "跨设备的个人 AI",
    text: "延续记忆，连接服务，主动协助。",
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
          <span className="eyebrow">产品愿景</span>
          <h2 id="vision-title">
            每个人，都有
            <br />
            自己的 AI 伙伴。
          </h2>
          <p>从工作到生活，随时提供帮助；换一台设备，也能延续对话和记忆。</p>
        </div>
      </div>
      <div className="vision-roadmap container">
        <div className="roadmap-grid">
          {roadmap.map((item) => (
            <article className="roadmap-item" key={item.step} data-reveal>
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
  { title: "个人订阅", text: "为模型、记忆和执行能力付费。" },
  { title: "专业与团队", text: "共享资料与流程，探索按席位付费。" },
  { title: "技能与服务", text: "连接第三方服务，探索服务分成。" },
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
          <span className="feature-status">商业规划</span>
          <h2 id="opportunity-title">商业路径</h2>
          <p>从个人订阅，扩展到团队与服务。</p>
          <div className="business-diagram">
            <BusinessVisual />
          </div>
        </div>
        <div className="business-paths">
          {businessPaths.map((item) => (
            <article className="business-option" key={item.title}>
              <h3>{item.title}</h3>
              <p>{item.text}</p>
            </article>
          ))}
        </div>
      </div>
      <div className="container closing-strip">
        <p>
          <strong>Mirally · Your Everyday Ally</strong>
        </p>
        <a className="text-link" href="#a-day">
          查看演示 <ArrowUpRight size={17} />
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
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (!chapters.some(([chapterId]) => chapterId === id)) return;
    // Wait for the first layout before locating an anchor mounted by React.
    let frame;
    const alignChapter = () => {
      frame = requestAnimationFrame(() => {
        document.getElementById(id)?.scrollIntoView({ behavior: "instant", block: "start" });
      });
    };
    if (document.readyState === "complete") alignChapter();
    else window.addEventListener("load", alignChapter, { once: true });
    return () => {
      window.removeEventListener("load", alignChapter);
      cancelAnimationFrame(frame);
    };
  }, []);
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
        <NextCapabilities />
        <Vision />
        <Opportunity />
      </main>
    </>
  );
}
