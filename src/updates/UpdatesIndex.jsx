import { ArrowRight } from "lucide-react";
import { Footer, Header } from "./chrome.jsx";
import { releases } from "./releases.js";

export default function UpdatesIndex() {
  return (
    <>
      <a className="skip-link" href="#main">
        跳到主要内容
      </a>
      <Header root="../" updatesHref="./" />
      <main id="main" className="container updates-page">
        <header className="updates-head">
          <h1>更新日志</h1>
          <p>Mirally 的版本更新记录。</p>
        </header>
        <ol className="release-rows">
          {releases.map((release) => (
            <li key={release.slug}>
              <a href={`./${release.slug}/`}>
                <time dateTime={release.date}>{release.date}</time>
                <span className="release-row-title">{release.title}</span>
                <ArrowRight size={15} aria-hidden="true" />
              </a>
            </li>
          ))}
        </ol>
      </main>
      <Footer root="../" />
    </>
  );
}
