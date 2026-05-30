import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发期把 /api 代理到后端，避免跨域；生产由 Caddy 同源反代（PR11）。
export default defineConfig({
  plugins: [react()],
  build: {
    // 不清空 outDir：dist/demo/ 下托管着 demo 视频(root 所有, 部署时放入)，
    // 清空会因权限失败、且会误删视频。资源文件名带 hash、index.html 每次覆盖，残留无害。
    emptyOutDir: false,
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
      },
    },
  },
  // preview（构建产物预览）也代理 /api，便于在 VM 上联调；生产由 Caddy 同源反代（PR11）。
  preview: {
    host: "0.0.0.0",
    port: 4173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
      },
    },
  },
});
