import { ImagePlus } from "lucide-react";
import { Footer, Header, NextStep } from "./chrome.jsx";
import { releases } from "./releases.js";

function Figure({ image }) {
  if (image.src) {
    return (
      <figure className="release-figure">
        <img src={image.src} alt={image.alt} loading="lazy" />
        <figcaption>{image.caption}</figcaption>
      </figure>
    );
  }
  return (
    <figure className="release-figure">
      <div
        className="release-figure-empty"
        role="img"
        aria-label={`图示占位：${image.alt}`}
      >
        <ImagePlus size={20} strokeWidth={1.4} aria-hidden="true" />
        <span>图示待补充</span>
      </div>
      <figcaption>{image.caption}</figcaption>
    </figure>
  );
}

function numberedSections(release) {
  let number = 0;
  return release.sections.map((section) => ({
    title: section.title,
    items: section.items.map((item) => {
      number += 1;
      return { ...item, number: String(number).padStart(2, "0") };
    }),
  }));
}

export default function ReleasePage({ release }) {
  const index = releases.findIndex((item) => item.slug === release.slug);
  const newer = index > 0 ? releases[index - 1] : null;
  const older =
    index >= 0 && index < releases.length - 1 ? releases[index + 1] : null;
  const sections = numberedSections(release);

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
            <p className="release-intro">{release.intro ?? release.summary}</p>
          </header>
          {sections.map((section) => (
            <section className="release-part" key={section.title}>
              <h2 className="release-part-title">{section.title}</h2>
              <ol className="release-points">
                {section.items.map((item) => (
                  <li className="release-point" key={item.title}>
                    <h3>
                      <span className="release-point-number" aria-hidden="true">
                        {item.number}
                      </span>
                      {item.title}
                    </h3>
                    <p>{item.text}</p>
                    {item.image && <Figure image={item.image} />}
                  </li>
                ))}
              </ol>
            </section>
          ))}
          <p className="release-outro">
            以上是本期更新。想了解现在能做什么，可以查看
            <a href="../../#already-here">当前能力</a>。
          </p>
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
