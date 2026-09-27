import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { isLoopback, localAuthState, proxyCredentialFor } from "./localDevAuth";

export default defineConfig(({ command, mode }) => {
  // loadEnv runs only in Vite's Node process. These unprefixed values are never
  // exposed via import.meta.env or serialized into a browser build.
  const env = loadEnv(mode, path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."), "");
  const target = process.env.API_PROXY_TARGET || env.API_PROXY_TARGET || "http://127.0.0.1:8000";
  const auth = localAuthState(command, target, { ...env, ...process.env });
  return {
    plugins: [react(), {
      name: "local-demo-auth-status",
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          const address = req.socket.remoteAddress;
          if ((req.url?.startsWith("/api/") || req.url?.startsWith("/__local_demo_auth")) && !isLoopback(address)) {
            res.statusCode = 403; res.end(); return;
          }
          if (req.url?.split("?")[0] === "/__local_demo_auth") {
            res.setHeader("Content-Type", "application/json");
            res.setHeader("Cache-Control", "no-store");
            res.end(JSON.stringify({ enabled: auth.enabled, error: auth.error }));
            return;
          }
          next();
        });
      },
    }],
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": {
          target,
          changeOrigin: true,
          configure(proxy) {
            proxy.on("proxyReq", (proxyReq, req) => {
              const credential = proxyCredentialFor(auth, req.socket.remoteAddress, req.method, req.url);
              if (credential) proxyReq.setHeader("Authorization", `Bearer ${credential}`);
            });
          },
        },
      },
    },
    test: { environment: "jsdom", setupFiles: ["./tests/setup.ts"] },
  };
});
