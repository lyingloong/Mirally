import { readdir, readFile, mkdir, writeFile } from "node:fs/promises";
import { dirname, extname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";

const root = fileURLToPath(new URL("../", import.meta.url));
const publicRoot = join(root, "public");
const sourceRoot = join(root, "src").split(sep).join("/") + "/";
const output = resolve(root, "exports", "Mirally.html");
const mimeTypes = {
  ".png": "image/png",
  ".webp": "image/webp",
  ".svg": "image/svg+xml",
  ".md": "text/plain;charset=utf-8",
  ".txt": "text/plain;charset=utf-8",
};
const embedded = new Map();

async function embedDirectory(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      await embedDirectory(path);
    } else if (entry.isFile()) {
      const name = "./" + relative(publicRoot, path).split(sep).join("/");
      const mime = mimeTypes[extname(path)] || "application/octet-stream";
      embedded.set(
        name,
        `data:${mime};base64,${(await readFile(path)).toString("base64")}`,
      );
    }
  }
}

await embedDirectory(publicRoot);
const credits = await Promise.all(
  ["CREDITS.md", "LICENSES.txt", "fonts/OFL.txt"].map((path) =>
    readFile(join(publicRoot, path), "utf8"),
  ),
);
embedded.set(
  "./CREDITS.md",
  "data:text/plain;charset=utf-8;base64," +
    Buffer.from(
      credits.join("\n\n----------------------------------------\n\n"),
    ).toString("base64"),
);

function inlinePublicAssets(text) {
  return text.replace(/(["'])(\.\/[^"']+)\1/g, (match, quote, path) => {
    if (embedded.has(path)) return quote + embedded.get(path) + quote;
    if (/\.(?:png|webp|svg|jpg|jpeg|md|txt)$/i.test(path)) {
      throw new Error(`Offline export is missing a public asset: ${path}`);
    }
    return match;
  });
}

const result = await build({
  configFile: false,
  root,
  base: "./",
  mode: "production",
  esbuild: { jsx: "automatic" },
  plugins: [
    {
      name: "mirally-offline-assets",
      enforce: "pre",
      transform(code, id) {
        if (
          !id.replaceAll("\\", "/").startsWith(sourceRoot) ||
          !/\.[jt]sx?$/.test(id)
        )
          return;
        // A download works from file://; top-level navigation to data: is blocked
        // by browsers. Include all third-party licenses in the same local download.
        code = code
          .replace(
            'href="./CREDITS.md" target="_blank" rel="noreferrer"',
            'href="./CREDITS.md" download="Mirally-素材来源与许可.txt"',
          )
          .replace("查看素材来源 <", "下载素材来源与许可 <");
        return { code: inlinePublicAssets(code), map: null };
      },
    },
  ],
  build: {
    write: false,
    copyPublicDir: false,
    target: "es2020",
    assetsInlineLimit: Infinity,
    cssCodeSplit: false,
    sourcemap: false,
    rollupOptions: {
      input: join(root, "src", "main.jsx"),
      output: {
        format: "iife",
        name: "MirallyShowcase",
        inlineDynamicImports: true,
      },
    },
  },
});

const outputs = (Array.isArray(result) ? result : [result]).flatMap(
  (item) => item.output,
);
const scripts = outputs.filter((item) => item.type === "chunk");
const styles = outputs.filter(
  (item) => item.type === "asset" && item.fileName.endsWith(".css"),
);
if (
  scripts.length !== 1 ||
  scripts[0].imports.length ||
  scripts[0].dynamicImports.length ||
  outputs.length !== scripts.length + styles.length
) {
  throw new Error(
    "Offline export must contain one inline script and no external assets.",
  );
}
const css = styles.map((item) => String(item.source)).join("\n");
for (const [, url] of css.matchAll(/url\(([^)]+)\)/g)) {
  if (!/^["']?(?:data:|#)/.test(url.trim())) {
    throw new Error(`Offline CSS still references an external asset: ${url}`);
  }
}

let html = await readFile(join(root, "index.html"), "utf8");
html = html
  .replace(/\s*<script\b[^>]*src=[^>]*><\/script>/g, "")
  .replace(/\s*<link\b[^>]*rel="preload"[^>]*>/g, "");
html = inlinePublicAssets(html)
  .replace(
    "</head>",
    () => `<style>${css.replace(/<\/style/gi, "<\\/style")}</style>\n</head>`,
  )
  .replace(
    "</body>",
    () =>
      `<script>${scripts[0].code.replace(/<\/script/gi, "<\\/script")}</script>\n</body>`,
  );

await mkdir(dirname(output), { recursive: true });
await writeFile(output, html, "utf8");
console.log(`Offline HTML: ${output}`);
console.log(
  `${(Buffer.byteLength(html) / 1024 / 1024).toFixed(2)} MiB; copy this file and open it in a modern browser.`,
);
