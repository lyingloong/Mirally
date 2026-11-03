import { ImagePlus } from "lucide-react";
import { Footer, Header, NextStep } from "./chrome.jsx";
import { releases } from "./releases.js";

function Figure({ item }) {
  if (item.src) {
    return (
      <figure className="release-figure">
        <img src={item.src} alt={item.alt} loading="lazy" />
        <figcaption>{item.caption}</figcaption>
      </figure>
    );
  }
  return (
    <figure className="release-figure">
      <div
        className="release-figure-empty"
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

export default function ReleasePage({ release }) {
  const index = releases.findIndex((item) => item.slug === release.slug);
  const newer = index > 0 ? releases[index - 1] : null;
  const older = index >= 0 && index < releases.length - 1 ? releases[index + 1] : null;

  return (
    <>
      <a className="skip-link" href="#main">
        跳到主要内容
      </a>
      <Header root="../../" updatesHref="../" />
      <main id="main">
        <article className="container release">
          <a className="release-back" href="../">
            ← 所有更新
          </a>
          <header className="release-head">
            <div className="release-meta">
              <time dateTime={release.date}>{release.date}</time>
              <span>{release.period}</span>
              {index === 0 && <span className="release-badge">最新</span>}
            </div>
            <h1>{release.title}</h1>
            <p className="release-summary">{release.summary}</p>
          </header>
          {release.sections.map((section) => (
            <section className="release-section" key={section.title}>
              <h2>{section.title}</h2>
              <ul className="release-items">
                {section.items.map((item) => (
                  <li key={item.title}>
                    <strong>{item.title}</strong>
                    <span>{item.text}</span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
          {release.media.length > 0 && (
            <div className="release-media">
              {release.media.map((item) => (
                <Figure key={item.alt} item={item} />
              ))}
            </div>
          )}
          {(older || newer) && (
            <nav className="release-nav" aria-label="其他更新">
              {older ? (
                <a href={`../${older.slug}/`}>
                  <span>上一期</span>
                  {older.title}
                </a>
              ) : (
                <span />
              )}
              {newer ? (
                <a className="next" href={`../${newer.slug}/`}>
                  <span>下一期</span>
                  {newer.title}
                </a>
              ) : (
                <span />
              )}
            </nav>
          )}
        </article>
        <NextStep root="../../" />
      </main>
      <Footer root="../../" />
    </>
  );
}
