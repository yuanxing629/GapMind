import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
export default defineConfig({
    plugins: [react()],
    // VITE_* 变量位于仓库根目录 .env 或被忽略的 .env.local 中。
    envDir: "../",
    resolve: {
        alias: {
            "@": path.resolve(__dirname, "./src"),
        },
    },
    server: {
        port: 5173,
        proxy: {
            "/api": {
                target: "http://localhost:8000",
                changeOrigin: true,
            },
        },
    },
});
