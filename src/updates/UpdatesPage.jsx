import { useEffect, useState } from "react";
import { ArrowRight, ArrowUpRight, ImagePlus } from "lucide-react";
import { updates } from "./entries.js";

const firstDate = updates[updates.length - 1]?.date ?? "";
const lastDate = updates[0]?.date ?? "";

function monthSpan() {
  if (!firstDate || !lastDate) return "";
  const start = firstDate.slice(0, 7).split("-");
  const end = lastDate.slice(0, 7).split("-");
  if (start[0] === end[0] && start[1] === end[1])
    return `${start[0]} 年 ${Number(start[1])} 月`;
  if (start[0] === end[0])
    return `${start[0]} 年 ${Number(start[1])}–${Number(end[1])} 月`;
  return `${start[0]} 年 ${Number(start[1])} 月 – ${end[0]} 年 ${Number(end[1])} 月`;
}

function Brand() {
  return (
    <span className="brand">
      <img src="../mirally.png" alt="" width="44" height="44" />
      <span>
        mirally<span className="brand-period">.</span>
      </span>
    </span>
  );
}

function Header() {
  return (
    <header className="site-header updates-header">
      <div className="header-inner">
        <a className="brand-link" href="../" aria-label="返回 Mirally 首页">
          <Brand />
        </a>
        <nav className="main-nav" aria-label="主导航">
          <a href="../#a-day">场景演示</a>
          <a href="../#already-here">当前能力</a>
          <a href="./" className="nav-active" aria-current="page">
            更新
          </a>
        </nav>
        <a className="header-cta" href="../">
          返回首页 <ArrowRight size={16} />
        </a>
      </div>
    </header>
  );
}

function UpdatesHero() {
  return (
    <section className="updates-hero">
      <div className="container">
        <span className="eyebrow">
          <span className="status-dot" /> PRODUCT UPDATES
        </span>
        <h1>更新</h1>
        <p className="updates-lede">按日期记录 Mirally 的每一次更新。</p>
        <div className="updates-meta">
          <span>{monthSpan()}</span>
          <span>{updates.length} 次更新</span>
        </div>
      </div>
    </section>
  );
}

function UpdatesIndex({ active }) {
  return (
    <aside className="updates-index" aria-label="更新目录">
      <p className="updates-index-title">更新记录</p>
      <ol>
        {updates.map((entry) => (
          <li key={entry.id}>
            <a
              href={`#${entry.id}`}
              aria-current={active === entry.id ? "true" : undefined}
            >
              <span className="updates-index-date">{entry.date}</span>
              <span className="updates-index-name">{entry.title}</span>
            </a>
          </li>
        ))}
      </ol>
    </aside>
  );
}

function Figure({ item }) {
  if (item.src) {
    return (
      <figure className="update-figure">
        <img src={item.src} alt={item.alt} loading="lazy" />
        <figcaption>{item.caption}</figcaption>
      </figure>
    );
  }
  return (
    <figure className="update-figure">
      <div
        className="update-figure-empty"
        role="img"
        aria-label={`图示占位：${item.alt}`}
      >
        <ImagePlus size={20} strokeWidth={1.4} aria-hidden="true" />
        <span>图示待补充</span>
      </div>
      <figcaption>{item.caption}</figcaption>
    </figure>
  );
}

function UpdateDocument({ entry, latest }) {
  const headingId = `update-title-${entry.id}`;
  return (
    <article
      className="update-doc"
      id={entry.id}
      aria-labelledby={headingId}
    >
      <header className="update-doc-head">
        <p className="update-doc-date">
          <time dateTime={entry.date}>{entry.date}</time>
          {latest && <span className="update-doc-badge">最新</span>}
        </p>
        <h2 id={headingId}>{entry.title}</h2>
        <p className="update-doc-summary">{entry.summary}</p>
      </header>
      {entry.sections
        .filter((section) => section.items.length > 0)
        .map((section) => (
          <section className="update-doc-section" key={section.title}>
            <h3>{section.title}</h3>
            <ul className="update-doc-list">
              {section.items.map((item) => (
                <li key={item.title}>
                  <strong>{item.title}</strong>
                  <span>{item.text}</span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      {entry.media.length > 0 && (
        <div className="update-doc-media">
          {entry.media.map((item) => (
            <Figure key={item.alt} item={item} />
          ))}
        </div>
      )}
    </article>
  );
}

function NextStep() {
  return (
    <section className="updates-next">
      <div className="container updates-next-inner">
        <p>想看看现在的 Mirally 能做什么？</p>
        <div>
          <a className="button button-dark" href="../#already-here">
            查看当前能力 <ArrowUpRight size={17} />
          </a>
          <a className="text-link" href="../#a-day">
            场景演示 <ArrowRight size={16} />
          </a>
        </div>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="site-footer updates-footer">
      <div className="container footer-inner">
        <a href="../" aria-label="返回 Mirally 首页">
          <Brand />
        </a>
        <p>© {new Date().getFullYear()} Mirally</p>
        <div>
          <a href="../">首页</a>
          <a href="../#a-day">场景演示</a>
        </div>
      </div>
    </footer>
  );
}

export default function UpdatesPage() {
  const [active, setActive] = useState(updates[0]?.id);

  useEffect(() => {
    if (!("IntersectionObserver" in window)) return;
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) setActive(entry.target.id);
        });
      },
      { rootMargin: "-15% 0px -70% 0px" },
    );
    updates.forEach((entry) => {
      const element = document.getElementById(entry.id);
      if (element) observer.observe(element);
    });
    return () => observer.disconnect();
  }, []);

  return (
    <>
      <a className="skip-link" href="#main">
        跳到主要内容
      </a>
      <Header />
      <main id="main">
        <UpdatesHero />
        <div className="container updates-layout">
          <UpdatesIndex active={active} />
          <div className="updates-documents">
            {updates.map((entry, index) => (
              <UpdateDocument
                key={entry.id}
                entry={entry}
                latest={index === 0}
              />
            ))}
          </div>
        </div>
        <NextStep />
      </main>
      <Footer />
    </>
  );
}
