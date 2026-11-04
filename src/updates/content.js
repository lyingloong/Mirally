import MarkdownIt from "markdown-it";

const FILES = import.meta.glob("../../content/updates/*.md", {
  query: "?raw",
  import: "default",
  eager: true,
});

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function parseFrontmatter(raw) {
  const match = raw.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/);
  if (!match) return { meta: {}, body: raw };
  const meta = {};
  for (const line of match[1].split(/\r?\n/)) {
    const index = line.indexOf(":");
    if (index === -1) continue;
    meta[line.slice(0, index).trim()] = line.slice(index + 1).trim();
  }
  return { meta, body: match[2] };
}

// Markdown 里的图片路径相对于 Markdown 文件；渲染到 `/updates/<slug>/` 时指向站点根。
function resolveImage(src) {
  const marker = "public/";
  const index = src.indexOf(marker);
  if (index === -1) return src;
  return `../../${src.slice(index + marker.length)}`;
}

const markdown = new MarkdownIt({ html: false, linkify: false });
const SYNTHETIC_PREFIX = "合成示意图：";

markdown.renderer.rules.image = (tokens, idx) => {
  const token = tokens[idx];
  const src = resolveImage(token.attrGet("src") ?? "");
  const alt = token.content ?? "";
  const synthetic = alt.startsWith(SYNTHETIC_PREFIX);
  const caption = synthetic ? alt.slice(SYNTHETIC_PREFIX.length) : alt;
  const tag = synthetic
    ? '<span class="figure-tag">合成示意图</span>'
    : "";
  return `<figure><img src="${escapeHtml(src)}" alt="${escapeHtml(alt)}"><figcaption>${tag}${escapeHtml(caption)}</figcaption></figure>`;
};

function renderMarkdown(body) {
  return markdown
    .render(body)
    .replace(/<p>\s*(<figure>[\s\S]*?<\/figure>)\s*<\/p>/g, "$1")
    .replace(/<p>\s*<\/p>/g, "");
}

export const releases = Object.entries(FILES)
  .map(([path, raw]) => {
    const slug = path.split("/").pop().replace(/\.md$/, "");
    const { meta, body } = parseFrontmatter(raw);
    return {
      slug,
      date: meta.date ?? slug,
      title: meta.title ?? "",
      html: renderMarkdown(body),
    };
  })
  .sort((a, b) => b.date.localeCompare(a.date));
