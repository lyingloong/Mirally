import { defineConfig } from "vite";

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
  build: { target: "es2020" },
});
