import { ArrowRight } from "lucide-react";
import { Footer, Header, NextStep } from "./chrome.jsx";
import { releases } from "./releases.js";

function ReleaseCard({ release }) {
  const counts = release.sections
    .map((section) => `${section.title} ${section.items.length}`)
    .join(" · ");
  return (
    <li>
      <a className="release-card" href={`./${release.slug}/`}>
        <div className="release-card-top">
          <time dateTime={release.date}>{release.date}</time>
          <span className="release-card-period">{release.period}</span>
        </div>
        <h2>{release.title}</h2>
        <p>{release.summary}</p>
        <div className="release-card-bottom">
          <span className="release-card-counts">{counts}</span>
          <span className="release-card-more">
            查看更新 <ArrowRight size={15} aria-hidden="true" />
          </span>
        </div>
      </a>
    </li>
  );
}

export default function UpdatesIndex() {
  return (
    <>
      <a className="skip-link" href="#main">
        跳到主要内容
      </a>
      <Header root="../" updatesHref="./" />
      <main id="main">
        <section className="updates-hero">
          <div className="container">
            <span className="eyebrow">
              <span className="status-dot" /> PRODUCT UPDATES
            </span>
            <h1>更新</h1>
            <p className="updates-lede">
              按开发周期记录 Mirally 的新功能、体验优化与修复。
            </p>
            <div className="updates-meta">
              <span>{releases.length} 期</span>
              <span>持续更新</span>
            </div>
          </div>
        </section>
        <section className="releases container" aria-label="更新列表">
          <ol className="release-list">
            {releases.map((release) => (
              <ReleaseCard key={release.slug} release={release} />
            ))}
          </ol>
        </section>
        <NextStep root="../" />
      </main>
      <Footer root="../" />
    </>
  );
}
