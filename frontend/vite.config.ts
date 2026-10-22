import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxy = {
    "/api": {
      target: process.env.ZHOUXU_API_TARGET || env.ZHOUXU_API_TARGET || "http://127.0.0.1:8010",
      changeOrigin: true,
      rewrite: (url: string) => url.replace(/^\/api/, ""),
    },
  };
  return {
    server: {
      host: "::",
      port: 8080,
      proxy,
      hmr: {
        overlay: false,
      },
    },
    preview: { proxy },
    plugins: [react(), mode === "development" && componentTagger()].filter(Boolean),
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
  };
});
