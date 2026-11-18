import DOMPurify from "dompurify";
import { stripUnsupportedCitationControlMarkers } from "../../../src/shared/text/citation-control-markers.js";
import { routeIdFromPath } from "../app-route-paths.ts";
import { resolveControlUiPaths } from "../app/browser.ts";
import { i18n, t } from "../i18n/index.ts";
import { truncateText } from "../lib/format.ts";
import { parseGitHubLinkTarget } from "./github-link-target.ts";
import { createAssistantTranscriptPlainTextFallback } from "./markdown-assistant-transcript.ts";
import { renderMarkdownCodeBlock } from "./markdown-code-blocks.ts";
import { isHostLocalMarkdownFileHref } from "./markdown-file-links.ts";
import { markdownGitHubAliasSignature } from "./markdown-github-repositories.ts";
import {
  prepareMarkdownHumanMentions,
  restoreMarkdownHumanMentions,
} from "./markdown-human-mentions.ts";
import type { MarkdownJson } from "./markdown-json.ts";
import { createMarkdownParser } from "./markdown-parser.ts";
import { stripProgressCardRawContentBlocks } from "./markdown-raw-content.ts";
import {
  MARKDOWN_PARSE_LIMIT,
  normalizeMarkdownRenderOptions,
  type MarkdownRenderEnv,
  type MarkdownRenderOptions,
} from "./markdown-render-options.ts";
import { repairStreamingMarkdownTail, splitStableStreamingMarkdown } from "./markdown-streaming.ts";
import { isMarkdownBlockArtText, normalizeMarkdownLineBreaks } from "./markdown-text.ts";

const allowedTags = [
  "a",
  "b",
  "blockquote",
  "br",
  "button",
  "code",
  "del",
  "details",
  "div",
  "em",
  "h1",
  "h2",
  "h3",
  "h4",
  "hr",
  "i",
  "input",
  "li",
  "ol",
  "mirally-person-reference",
  "p",
  "pre",
  "s",
  "span",
  "strong",
  "summary",
  "table",
  "tbody",
  "td",
  "th",
  "thead",
  "tr",
  "ul",
  "img",
];

const allowedAttrs = [
  "checked",
  "profile-id",
  "label",
  "class",
  "disabled",
  "href",
  "open",
  "rel",
  "target",
  "tabindex",
  "title",
  "start",
  "src",
  "alt",
  "data-code",
  "data-code-encoding",
  "data-file-kind",
  "data-file-line",
  "data-file-path",
  "data-link-favicon-host",
  "data-session-key",
  "data-session-href",
  "data-table-interactions",
  "type",
  "aria-expanded",
  "aria-label",
  "aria-pressed",
  "role",
];
const sanitizeOptions = {
  ALLOWED_TAGS: allowedTags,
  ALLOWED_ATTR: allowedAttrs,
  ADD_DATA_URI_TAGS: ["img"],
};
const progressSanitizeOptions = {
  ...sanitizeOptions,
  ALLOWED_TAGS: [...allowedTags, "progress"],
  ALLOWED_ATTR: [...allowedAttrs, "value", "max"],
};

let hooksInstalled = false;
const MARKDOWN_CHAR_LIMIT = 140_000;
// Covers several message-heavy sessions during rapid switching. Only inputs
// up to 50k characters enter this 500-entry LRU, keeping memory bounded.
const MARKDOWN_CACHE_LIMIT = 500;
const MARKDOWN_CACHE_MAX_CHARS = 50_000;
const markdownCache = new Map<string, string>();
const STREAMING_INPUT_CACHE_LIMIT = 8;
const STREAMING_INPUT_CACHE_MAX_CHARS = MARKDOWN_CHAR_LIMIT;
type StreamingInputCacheEntry = { source: string; normalized: string };
const streamingInputCache = new Map<string, StreamingInputCacheEntry>();

function getCachedMarkdown(key: string): string | null {
  const cached = markdownCache.get(key);
  if (cached === undefined) {
    return null;
  }
  markdownCache.delete(key);
  markdownCache.set(key, cached);
  return cached;
}

function setCachedMarkdown(key: string, value: string) {
  markdownCache.set(key, value);
  if (markdownCache.size <= MARKDOWN_CACHE_LIMIT) {
    return;
  }
  const oldest = markdownCache.keys().next().value;
  if (oldest) {
    markdownCache.delete(oldest);
  }
}

function normalizeStreamingMarkdownInput(markdownLocal: string, streamKey?: string): string {
  const source = stripUnsupportedCitationControlMarkers(markdownLocal);
  if (!streamKey) {
    return normalizeMarkdownLineBreaks(source);
  }

  const cached = streamingInputCache.get(streamKey);
  if (cached && source.startsWith(cached.source)) {
    const appended = source.slice(cached.source.length);
    const normalizedAppend = normalizeMarkdownLineBreaks(appended);
    const normalized =
      cached.source.endsWith("\r") && appended.startsWith("\n")
        ? `${cached.normalized.slice(0, -1)}${normalizedAppend}`
        : `${cached.normalized}${normalizedAppend}`;
    streamingInputCache.delete(streamKey);
    if (source.length <= STREAMING_INPUT_CACHE_MAX_CHARS) {
      streamingInputCache.set(streamKey, { source, normalized });
    }
    return normalized;
  }

  const normalized = normalizeMarkdownLineBreaks(source);
  streamingInputCache.delete(streamKey);
  if (source.length <= STREAMING_INPUT_CACHE_MAX_CHARS) {
    streamingInputCache.set(streamKey, { source, normalized });
  }
  while (streamingInputCache.size > STREAMING_INPUT_CACHE_LIMIT) {
    const oldest = streamingInputCache.keys().next().value;
    if (oldest === undefined) {
      break;
    }
    streamingInputCache.delete(oldest);
  }
  return normalized;
}

function isControlUiRoutePath(pathname: string): boolean {
  if (routeIdFromPath(pathname) !== null) {
    return true;
  }
  const basePath = currentControlUiBasePath();
  if (!basePath) {
    return false;
  }
  if (pathname !== basePath && !pathname.startsWith(`${basePath}/`)) {
    return false;
  }
  return routeIdFromPath(pathname, basePath) !== null;
}

function currentControlUiBasePath(): string {
  if (typeof window === "undefined") {
    return "";
  }
  return resolveControlUiPaths(window.location.pathname)[0];
}

function hasMarkdownContentName(node: Node): boolean {
  if (node.nodeType === Node.TEXT_NODE) {
    return Boolean(node.textContent?.trim());
  }
  if (
    !(node instanceof Element) ||
    node.getAttribute("aria-hidden")?.trim().toLowerCase() === "true"
  ) {
    return false;
  }
  if (node.matches("img[alt]")) {
    return Boolean(node.getAttribute("alt")?.trim());
  }
  if (node.matches("progress")) {
    // A progress value names its containing link; fallback text does not.
    const valueText = node.getAttribute("aria-valuetext");
    if (valueText !== null) {
      return Boolean(valueText.trim());
    }
    return (
      node.hasAttribute("value") ||
      node.hasAttribute("aria-valuenow") ||
      Boolean(node.getAttribute("aria-label")?.trim() || node.getAttribute("title")?.trim())
    );
  }
  return [...node.childNodes].some(hasMarkdownContentName);
}

function installHooks() {
  if (hooksInstalled) {
    return;
  }
  hooksInstalled = true;

  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (!(node instanceof HTMLAnchorElement)) {
      return;
    }
    const href = node.getAttribute("href");
    if (!href) {
      return;
    }

    if (isHostLocalMarkdownFileHref(href)) {
      node.removeAttribute("href");
      return;
    }

    // Block dangerous URL schemes (javascript:, data:, vbscript:, etc.)
    try {
      const url = new URL(href, document.baseURI);
      if (url.protocol !== "http:" && url.protocol !== "https:" && url.protocol !== "mailto:") {
        node.removeAttribute("href");
        return;
      }
      if (parseGitHubLinkTarget(url.href)) {
        for (const element of [node, ...node.querySelectorAll("[title]")]) {
          const title = element.getAttribute("title");
          // A progress control needs a label; its value only names an enclosing link.
          const hasContentName = !element.matches("progress") && hasMarkdownContentName(element);
          if (title && !hasContentName && !element.getAttribute("aria-label")?.trim()) {
            element.setAttribute("aria-label", title);
          }
          // The rendered content owns the name; native hints must not survive preview closure.
          element.removeAttribute("title");
        }
      }
      if (url.origin === window.location.origin && isControlUiRoutePath(url.pathname)) {
        node.removeAttribute("rel");
        node.removeAttribute("target");
        return;
      }
    } catch {
      // Relative URLs are fine; malformed absolute URLs with dangerous schemes
      // will fail to parse and keep their href — but DOMPurify already strips
      // javascript: by default. This is defense-in-depth.
    }

    node.setAttribute("rel", "noreferrer noopener");
    node.setAttribute("target", "_blank");
  });
}

function appendMarkdownTruncationNotice(truncated: {
  text: string;
  truncated: boolean;
  total: number;
}): string {
  const notice = truncated.truncated
    ? `\n\n${t("chat.markdown.truncated", {
        total: String(truncated.total),
        shown: String(truncated.text.length),
      })}`
    : "";
  return `${truncated.text}${notice}`;
}

const markdownParser = createMarkdownParser();

// Uncached render core shared by the static and streaming paths. The streaming
// tail changes on every delta, so routing it through here (instead of the cached
// wrapper) keeps per-message churn out of the LRU cache.
function renderSanitizedMarkdown(renderInput: string, renderOptions: MarkdownRenderEnv): string {
  installHooks();
  const activeSanitizeOptions = renderOptions.progressBars
    ? progressSanitizeOptions
    : sanitizeOptions;
  const documentMode = renderOptions.mode === "document";
  const truncated = documentMode
    ? { text: renderInput, truncated: false, total: renderInput.length }
    : truncateText(renderInput, MARKDOWN_CHAR_LIMIT);
  const input = renderOptions.progressBars
    ? stripProgressCardRawContentBlocks(appendMarkdownTruncationNotice(truncated))
    : appendMarkdownTruncationNotice(truncated);
  if (isMarkdownBlockArtText(truncated.text)) {
    return DOMPurify.sanitize(
      renderMarkdownCodeBlock(input, "", renderOptions, { blockArt: true }),
      activeSanitizeOptions,
    );
  }
  if (!documentMode && truncated.text.length > MARKDOWN_PARSE_LIMIT) {
    // Large plain-text replies should stay readable without inheriting the
    // capped code-block chrome, while still preserving whitespace for logs
    // and other structured text that commonly trips the parse guard.
    return DOMPurify.sanitize(toPlainTextElement(input, renderOptions), activeSanitizeOptions);
  }
  let rendered: string | HTMLDivElement;
  try {
    rendered = markdownParser.render(input, renderOptions);
  } catch (err) {
    // Fall back to escaped plain text when md.render() throws (#36213).
    console.warn("[markdown] md.render failed, falling back to plain text:", err);
    rendered = toPlainTextElement(input, renderOptions);
  }
  return DOMPurify.sanitize(rendered, activeSanitizeOptions);
}

// Bare JSON bypasses Markdown normalization, which can alter literal Unicode separators.
// Both inputs still use the same code-block renderer and sanitizer boundary.
export function toSanitizedJsonHtml(json: MarkdownJson, options: MarkdownRenderOptions): string {
  installHooks();
  return DOMPurify.sanitize(
    // HTML parsing normalizes literal CRs; character references survive both
    // sanitizer parsing and the final unsafeHTML commit without changing Raw.
    renderMarkdownCodeBlock(json.text, "json", normalizeMarkdownRenderOptions(options), {
      json,
    }).replaceAll("\r", "&#13;"),
    sanitizeOptions,
  ).replaceAll("\r", "&#13;");
}

export function toSanitizedMarkdownHtml(
  markdownLocal: string,
  options: MarkdownRenderOptions = {},
): string {
  const renderOptions = normalizeMarkdownRenderOptions(options);
  const prepared =
    renderOptions.mode === "document" || markdownLocal.length <= MARKDOWN_PARSE_LIMIT
      ? prepareMarkdownHumanMentions(
          markdownLocal,
          renderOptions.humanMentions,
          markdownParser.utils.normalizeReference,
        )
      : { source: markdownLocal, tokens: [] };
  renderOptions.humanMentionTokens = prepared.tokens;
  const renderInput = normalizeMarkdownLineBreaks(
    stripUnsupportedCitationControlMarkers(prepared.source),
  );
  if (!renderInput.trim()) {
    return "";
  }
  if (renderInput.length > MARKDOWN_CACHE_MAX_CHARS) {
    return renderSanitizedMarkdown(renderInput, renderOptions);
  }
  const cacheKey = `${i18n.getLocale()}\0${renderOptions.assistantTranscriptRoleHeaders}\0${renderOptions.codeBlockChrome}\0${renderOptions.codeBlockInteraction}\0${renderOptions.fileLinks}\0${JSON.stringify(renderOptions.githubRepo ? [renderOptions.githubRepo.owner, renderOptions.githubRepo.repo] : null)}\0${markdownGitHubAliasSignature(renderOptions.githubRepositories, renderOptions.githubRepo)}\0${renderOptions.interactiveImages}\0${renderOptions.linkFavicons}\0${renderOptions.progressBars}\0${renderOptions.mode}\0${renderOptions.remoteImages}\0${renderOptions.sessionLinks}\0${renderOptions.tableInteractions}\0${JSON.stringify(renderOptions.humanMentionTokens)}\0${renderInput}`;
  const cached = getCachedMarkdown(cacheKey);
  if (cached !== null) {
    return cached;
  }
  const sanitized = renderSanitizedMarkdown(renderInput, renderOptions);
  setCachedMarkdown(cacheKey, sanitized);
  return sanitized;
}

function toPlainTextElement(value: string, options: MarkdownRenderEnv): HTMLDivElement {
  return createAssistantTranscriptPlainTextFallback(
    restoreMarkdownHumanMentions(normalizeMarkdownLineBreaks(value), options.humanMentionTokens),
    options.assistantTranscriptRoleHeaders,
    () => t("sessionsView.assistant"),
  );
}

export function toStreamingMarkdownParts(
  markdownLocal: string,
  options: MarkdownRenderOptions = {},
  streamKey?: string,
): [stableHtml: string, tailHtml: string] {
  const renderOptions = normalizeMarkdownRenderOptions(options);
  // Explicit selections are complete user input, not incremental assistant text.
  if (renderOptions.humanMentions.length) {
    return [toSanitizedMarkdownHtml(markdownLocal, options), ""];
  }
  const rawInput = normalizeStreamingMarkdownInput(markdownLocal, streamKey);
  if (isMarkdownBlockArtText(rawInput)) {
    return ["", renderSanitizedMarkdown(rawInput, renderOptions)];
  }

  if (!rawInput.trim()) {
    return ["", ""];
  }
  const truncated = truncateText(rawInput, MARKDOWN_CHAR_LIMIT);
  const input = appendMarkdownTruncationNotice(truncated);

  const { boundary, tailRepairStart } = splitStableStreamingMarkdown(
    input,
    streamKey,
    truncated.text.length,
  );
  const stableMarkdown = input.slice(0, boundary);
  const streamingTail = input.slice(boundary);
  const stableHtml = boundary > 0 ? toSanitizedMarkdownHtml(stableMarkdown, options) : "";
  if (!streamingTail.trim()) {
    return [stableHtml, ""];
  }
  const tailHtml =
    tailRepairStart === null
      ? renderSanitizedMarkdown(streamingTail, { ...renderOptions, streamingOpenFence: true })
      : renderSanitizedMarkdown(
          repairStreamingMarkdownTail(streamingTail, tailRepairStart - boundary),
          renderOptions,
        );
  return [stableHtml, tailHtml];
}
