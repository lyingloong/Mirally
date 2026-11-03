import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { defineConfig } from "vite";

const root = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  base: "./",
  esbuild: { jsx: "automatic" },
  server: {
    host: "127.0.0.1",
    watch: {
      ignored: ["**/.artifacts/**", "**/image-prompts/**", "**/*.md"],
      usePolling: process.platform === "win32",
      interval: 300,
    },
  },
  preview: { host: "127.0.0.1" },
  build: {
    target: "es2020",
    rollupOptions: {
      input: {
        main: resolve(root, "index.html"),
        updates: resolve(root, "updates", "index.html"),
      },
    },
  },
});
