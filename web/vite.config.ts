import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

const buildId = process.env.TIANSHU_UI_BUILD_ID?.trim() || 'dev-local';
const base = process.env.TIANSHU_UI_BASE?.trim() || (buildId !== 'dev-local' ? '/_ui/releases/' + buildId + '/' : '/');

export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  plugins: [react()],
  base,
  define: {
    __TIANSHU_UI_BUILD_ID__: JSON.stringify(buildId),
  },
  server: { port: 5173, strictPort: true },
  preview: { port: 4173, strictPort: true },
  build: { outDir: "dist", emptyOutDir: true },
});
