import { useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Expand,
  Github,
  X,
} from "lucide-react";
import DayExperience from "./DayExperience.jsx";

const repository = "https://github.com/lyingloong/Mirally";
const homepage = "https://lyingloong.github.io/";
const isOffline = typeof __MIRALLY_OFFLINE__ !== "undefined";
const updatesHref = isOffline ? null : "./updates/";

function downloadOfflineCredits(event) {
  if (!isOffline) return;
  event.preventDefault();
  // The offline exporter embeds the credits and all licenses as UTF-8 bytes.
  // Use the same Blob download path as the demo summary, including in file://.
  const encoded = event.currentTarget.href.split(",")[1];
  const bytes = Uint8Array.from(atob(encoded), (character) =>
    character.charCodeAt(0),
  );
  const url = URL.createObjectURL(
    new Blob([bytes], { type: "text/plain;charset=utf-8" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "Mirally-素材来源与许可.txt";
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function Brand() {
  return (
    <span className="brand">
      <img src="./mirally.png" alt="" width="36" height="36" />
      <span>
        mirally<span className="brand-period">.</span>
      </span>
    </span>
  );
}

function Header() {
  return (
    <header className="site-header">
      <div className="header-inner">
        <a className="brand-link" href="#top" aria-label="Mirally 首页">
          <Brand />
        </a>
        <nav className="main-nav" aria-label="主导航">
          <a href="#a-day">演示</a>
          {updatesHref && <a href={updatesHref}>更新</a>}
          <a href={repository}>
            GitHub <ArrowUpRight size={13} aria-hidden="true" />
          </a>
        </nav>
        <a className="author-link" href={homepage}>
          <ArrowLeft size={13} aria-hidden="true" /> Hao Wang
        </a>
      </div>
    </header>
  );
}

function ProductPreview() {
  const dialog = useRef(null);
  const trigger = useRef(null);
  const close = () => {
    dialog.current?.close();
    trigger.current?.focus({ preventScroll: true });
  };
  return (
    <figure className="product-preview">
      <button
        className="product-image-button"
        ref={trigger}
        onClick={() => dialog.current?.showModal()}
        aria-label="放大 Mirally 开发版界面"
      >
        <img
          src="./images/product-desktop.webp"
          alt="Mirally 开发版的真实界面：左侧为会话和日历、活动、记忆入口，右侧为隔离演示会话与输入区。"
          width="1280"
          height="720"
          fetchPriority="high"
        />
        <span className="image-expand">
          <Expand size={16} aria-hidden="true" />
        </span>
      </button>
      <figcaption>
        <span>一个窗口，连接对话与日常。</span>
        <span>开发版界面 · 演示数据</span>
      </figcaption>
      <dialog
        className="product-dialog"
        ref={dialog}
        aria-label="Mirally 开发版界面"
        onClick={(event) => {
          if (event.target === event.currentTarget) close();
        }}
        onCancel={(event) => {
          event.preventDefault();
          close();
        }}
      >
        <button
          className="dialog-close"
          onClick={close}
          aria-label="关闭放大界面"
          autoFocus
        >
          <X size={20} />
        </button>
        <img
          src="./images/product-desktop.webp"
          alt="Mirally 开发版界面，隔离演示会话。"
          width="1280"
          height="720"
        />
        <p>Mirally 开发版 · 演示数据</p>
      </dialog>
    </figure>
  );
}

function Hero() {
  return (
    <section
      className="project-intro container"
      id="top"
      aria-labelledby="hero-title"
    >
      <div className="intro-copy">
        <p className="eyebrow">A PERSONAL AGENT, IN THE MAKING</p>
        <h1 id="hero-title" lang="en">
          Your everyday <span>ally.</span>
        </h1>
        <p className="intro-description">
          工作时并肩，生活里相伴。
          <br />
          一个围绕对话、记忆与行动，持续生长的个人 AI。
        </p>
        <div className="intro-actions">
          <a className="button button-dark" href="#a-day">
            看看它的日常 <ArrowDown size={15} aria-hidden="true" />
          </a>
          <a className="text-link" href={repository}>
            <Github size={16} aria-hidden="true" /> 探索源码
          </a>
        </div>
        <p className="project-status">
          <span aria-hidden="true" /> 开发中 <i aria-hidden="true">/</i> Windows
          桌面应用
        </p>
      </div>
      <ProductPreview />
    </section>
  );
}

const features = [
  {
    number: "01",
    title: "留在手边的伙伴",
    text: "主窗口、侧栏与桌宠，共用同一段对话。需要时展开，专注时退到一旁。",
  },
  {
    number: "02",
    title: "从对话到结果",
    text: "带上资料一起讨论，整理文件、安排提醒，让交流接着往下走。",
  },
  {
    number: "03",
    title: "记忆由你管理",
    text: "查看、补充和纠正记忆。活动记录按需开启，留下什么，由你决定。",
  },
];

function Foundations() {
  return (
    <section
      className="foundations container"
      id="already-here"
      aria-labelledby="foundations-title"
    >
      <div className="section-heading">
        <p className="eyebrow">BUILT AROUND YOUR DAY</p>
        <h2 id="foundations-title">从一段对话，延续到每一天。</h2>
      </div>
      <div className="feature-columns">
        {features.map(({ number, title, text }) => (
          <article className="feature-item" key={number}>
            <span className="feature-number" aria-hidden="true">
              {number}
            </span>
            <h3>{title}</h3>
            <p>{text}</p>
          </article>
        ))}
      </div>
      <div className="project-foundation">
        <span>为扩展留出空间</span>
        <p>支持 Skill、MCP 与可信插件，模型连接自行配置。</p>
        <a href={`${repository}/blob/main/docs/architecture/overview.md`}>
          项目架构 <ArrowUpRight size={14} aria-hidden="true" />
        </a>
      </div>
    </section>
  );
}

function ProjectNotes() {
  return (
    <section
      className="project-notes container"
      id="vision"
      aria-labelledby="notes-title"
    >
      <div className="notes-intro">
        <p className="eyebrow">A WORK IN PROGRESS</p>
        <h2 id="notes-title">
          慢慢成为，
          <br />
          更懂你的伙伴。
        </h2>
        <p>
          Mirally
          正在持续开发。我们关心的下一步，是让记忆、主动协助与日常任务更自然地连接起来。
        </p>
        <a
          className="text-link"
          href={`${repository}/blob/main/docs/product/vision.md`}
        >
          阅读项目思考 <ArrowUpRight size={15} aria-hidden="true" />
        </a>
      </div>
      <div className="notes-links">
        <h3>继续了解</h3>
        {updatesHref && (
          <a href={updatesHref}>
            <span>
              开发记录<small>最近的变化与进展</small>
            </span>
            <ArrowRight size={18} aria-hidden="true" />
          </a>
        )}
        <a href={repository}>
          <span>
            GitHub<small>源码、文档与讨论</small>
          </span>
          <ArrowUpRight size={18} aria-hidden="true" />
        </a>
        <a href={homepage}>
          <span>
            Hao Wang<small>关于作者与其他项目</small>
          </span>
          <ArrowUpRight size={18} aria-hidden="true" />
        </a>
      </div>
    </section>
  );
}

function Footer() {
  const [creditsOpen, setCreditsOpen] = useState(false);
  const dialog = useRef(null);
  const trigger = useRef(null);
  useEffect(() => {
    if (creditsOpen) dialog.current?.showModal();
    else dialog.current?.close();
  }, [creditsOpen]);
  const close = () => {
    setCreditsOpen(false);
    trigger.current?.focus({ preventScroll: true });
  };
  return (
    <>
      <footer className="site-footer">
        <div className="container footer-inner">
          <a href="#top" aria-label="返回 Mirally 首页">
            <Brand />
          </a>
          <p>Your everyday ally.</p>
          <button ref={trigger} onClick={() => setCreditsOpen(true)}>
            关于演示与素材 <ArrowUpRight size={13} aria-hidden="true" />
          </button>
        </div>
      </footer>
      <dialog
        className="credits-dialog"
        ref={dialog}
        aria-labelledby="credits-title"
        onCancel={(event) => {
          event.preventDefault();
          close();
        }}
        onClick={(event) => {
          if (event.target === event.currentTarget) close();
        }}
      >
        <button
          className="dialog-close"
          onClick={close}
          aria-label="关闭展示说明"
          autoFocus
        >
          <X size={20} />
        </button>
        <h2 id="credits-title">关于演示与素材</h2>
        <p>
          首页主图来自 Mirally
          开发版的隔离演示会话。交互演示使用预设资料，不连接模型、桌面或个人数据；生活陪伴场景属于概念展示。
        </p>
        <p>市集资料、人物与剧集均为虚构，下载的会议摘要也使用演示数据。</p>
        <a
          className="text-link"
          href="./CREDITS.md"
          onClick={downloadOfflineCredits}
          download={isOffline ? "Mirally-素材来源与许可.txt" : undefined}
          target={isOffline ? undefined : "_blank"}
          rel={isOffline ? undefined : "noreferrer"}
        >
          {isOffline ? "下载素材来源与许可" : "查看素材来源"}{" "}
          <ArrowUpRight size={14} aria-hidden="true" />
        </a>
      </dialog>
    </>
  );
}

export default function App() {
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (!id) return;
    let frame;
    const align = () => {
      frame = requestAnimationFrame(() =>
        document.getElementById(id)?.scrollIntoView({ behavior: "instant" }),
      );
    };
    if (document.readyState === "complete") align();
    else window.addEventListener("load", align, { once: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("load", align);
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
        <Foundations />
        <DayExperience />
        <ProjectNotes />
      </main>
      <Footer />
    </>
  );
}
