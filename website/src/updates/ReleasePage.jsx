import { Footer, Header } from "./chrome.jsx";
import { releases } from "./content.js";

export default function ReleasePage({ release }) {
  const index = releases.findIndex((item) => item.slug === release.slug);
  const newer = index > 0 ? releases[index - 1] : null;
  const older =
    index >= 0 && index < releases.length - 1 ? releases[index + 1] : null;

  return (
    <div className="updates-shell">
      <a className="skip-link" href="#main">
        跳到主要内容
      </a>
      <Header root="../../" updatesHref="../" />
      <main id="main">
        <article className="container release">
          <a className="release-back" href="../">
            ← 更新日志
          </a>
          <header className="release-head">
            <h1>
              <time dateTime={release.date}>{release.date}</time>
            </h1>
            <p className="release-title">{release.title}</p>
          </header>
          <div
            className="release-body"
            dangerouslySetInnerHTML={{ __html: release.html }}
          />
          {(older || newer) && (
            <nav className="release-nav" aria-label="其他更新">
              {older ? (
                <a href={`../${older.slug}/`}>
                  <span>上一期</span>
                  {older.date} {older.title}
                </a>
              ) : (
                <span />
              )}
              {newer ? (
                <a className="next" href={`../${newer.slug}/`}>
                  <span>下一期</span>
                  {newer.date} {newer.title}
                </a>
              ) : (
                <span />
              )}
            </nav>
          )}
        </article>
      </main>
      <Footer root="../../" />
    </div>
  );
}
