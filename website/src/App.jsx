import { useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowLeft,
  ArrowUpRight,
  ChevronDown,
  Expand,
  Github,
  MessageSquare,
  PanelRight,
  Sparkles,
  X,
} from "lucide-react";
import DayExperience from "./DayExperience.jsx";

const repository = "https://github.com/lyingloong/Mirally";
const homepage = "https://lyingloong.github.io/";
const installHref = `${repository}/blob/main/docs/architecture/overview.md#启动与准备`;
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
          <a href="#already-here">产品</a>
          <a href="#a-day">演示</a>
          {updatesHref && <a href={updatesHref}>更新</a>}
        </nav>
        <a className="header-cta product-header-cta" href={repository}>
          GitHub <ArrowUpRight size={14} aria-hidden="true" />
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
      <div className="product-stage">
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
      </div>
      <figcaption>
        <span>对话、材料与下一步，在同一个地方。</span>
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
        <div className="intro-heading">
          <p className="eyebrow" lang="en">
            YOUR EVERYDAY ALLY
          </p>
          <h1 id="hero-title">
            工作时并肩，
            <br />
            生活里相伴<span className="title-period">。</span>
          </h1>
        </div>
        <div className="intro-detail">
          <p className="intro-description">
            留在手边的个人 AI。
            <br />
            带上资料讨论，把想法变成行动，
            <br className="desktop-break" />
            也让重要的事，有处可寻。
          </p>
          <div className="intro-actions">
            <a className="button button-dark" href={repository}>
              <Github size={17} aria-hidden="true" /> 探索 Mirally
              <ArrowUpRight size={16} aria-hidden="true" />
            </a>
            <a className="text-link" href="#a-day">
              体验演示 <ArrowDown size={15} aria-hidden="true" />
            </a>
          </div>
          <p className="project-status">开发中 · Windows · 源码可用</p>
        </div>
      </div>
      <ProductPreview />
    </section>
  );
}

const features = [
  {
    title: "带上材料，把事情往前推进。",
    text: "文件、截图和想法都能加入对话。一起梳理内容、整理文件，把讨论接到具体的下一步。",
  },
  {
    title: "重要的事，留给记忆与提醒。",
    text: "查看和纠正记忆，为日程安排提醒。活动记录按需开启，记住什么，由你管理。",
  },
  {
    title: "换一种形态，继续同一段对话。",
    text: "从主窗口到侧栏，再到桌面上的小伙伴。对话、草稿与材料跟着你，不必重新开始。",
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
        <p className="eyebrow" lang="en">
          MADE FOR YOUR EVERYDAY
        </p>
        <h2 id="foundations-title">认真做事，也安静相伴。</h2>
      </div>
      <div className="foundation-layout">
        <div className="companion-portrait">
          <span className="portrait-label">Mirally 桌面伙伴</span>
          <div className="companion-art">
            <img
              src="./images/mirally-pet-otter.svg"
              alt="Mirally 的蓝色海獭桌宠"
              width="180"
              height="216"
              loading="lazy"
            />
          </div>
          <p>
            在你手边，
            <br />
            也给你留出空间。
          </p>
          <div className="companion-forms" aria-label="三种伙伴形态">
            <span>
              <MessageSquare size={16} aria-hidden="true" />
              主窗口
            </span>
            <span>
              <PanelRight size={16} aria-hidden="true" />
              侧栏
            </span>
            <span>
              <Sparkles size={16} aria-hidden="true" />
              桌宠
            </span>
          </div>
        </div>
        <div className="feature-list">
          {features.map(({ title, text }, index) => (
            <article className="feature-item" key={title}>
              <span className="feature-number" aria-hidden="true">
                0{index + 1}
              </span>
              <div>
                <h3>{title}</h3>
                <p>{text}</p>
              </div>
            </article>
          ))}
        </div>
      </div>
      <div className="extension-note">
        <div>
          <h3>按你的方式，继续扩展。</h3>
          <p>连接自己的模型，通过 Skill、MCP 与可信插件接入更多能力。</p>
        </div>
        <a
          className="text-link"
          href={`${repository}/blob/main/docs/architecture/extensions.md`}
        >
          了解扩展方式 <ArrowUpRight size={16} aria-hidden="true" />
        </a>
      </div>
    </section>
  );
}

const questions = [
  {
    title: "现在可以使用吗？",
    answer: (
      <>
        可以按安装指南从源码运行。目前面向 Windows，需要 Python 3.10+、Node.js
        24+ 和 WebView2；模型服务需要自行配置。暂未提供独立安装包。
      </>
    ),
  },
  {
    title: "网页演示就是真实产品吗？",
    answer: (
      <>
        首页截图来自 Mirally
        开发版，使用隔离的演示会话。下方交互使用预设资料与回答，不连接模型或个人数据；“生活”场景展示的是未来概念。
      </>
    ),
  },
  {
    title: "我的资料会发到哪里？",
    answer: (
      <>
        会话、记忆与配置由本机宿主管理。使用外部模型或工具服务时，处理任务所需的内容可能发送给相应服务。活动采集默认关闭，可在产品中按需开启。
      </>
    ),
  },
];

function GettingStarted() {
  return (
    <section
      className="getting-started container"
      id="start"
      aria-labelledby="start-title"
    >
      <div className="start-copy">
        <p className="eyebrow" lang="en">
          MAKE IT YOURS
        </p>
        <h2 id="start-title">从你的下一件事开始。</h2>
        <p>了解源码，把 Mirally 带到自己的桌面。</p>
        <a className="button button-dark" href={installHref}>
          查看安装指南 <ArrowUpRight size={16} aria-hidden="true" />
        </a>
        <a className="text-link" href={repository}>
          <Github size={17} aria-hidden="true" /> GitHub
        </a>
      </div>
      <div className="start-questions">
        {questions.map(({ title, answer }) => (
          <details key={title}>
            <summary>
              {title}
              <ChevronDown size={18} aria-hidden="true" />
            </summary>
            <p>{answer}</p>
          </details>
        ))}
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
        <div className="container footer-main">
          <div className="footer-brand">
            <a href="#top" aria-label="返回 Mirally 首页">
              <Brand />
            </a>
            <p>Your everyday ally.</p>
          </div>
          <nav className="footer-links" aria-label="项目资源">
            <a href={repository}>GitHub</a>
            {updatesHref && <a href={updatesHref}>更新记录</a>}
            <a href={`${repository}/blob/main/docs/product/vision.md`}>
              产品思考
            </a>
          </nav>
        </div>
        <div className="container footer-bottom">
          <a className="footer-author" href={homepage}>
            <ArrowLeft size={14} aria-hidden="true" /> Hao Wang
          </a>
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
        <DayExperience />
        <Foundations />
        <GettingStarted />
      </main>
      <Footer />
    </>
  );
}
