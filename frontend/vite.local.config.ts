import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { optimumProxy } from "./lib/optimum-proxy";

export default defineConfig(({ mode }) => {
  // These values are used only inside the Node.js server.
  const settings = loadEnv(mode, process.cwd(), "BROADBAND_");

  const base = settings.BROADBAND_API_BASE_URL?.trim();
  const key = settings.BROADBAND_API_KEY?.trim();

  const routes: Record<string, string> = {
    "/api/plans/jio": "/api/jio/plans",
    "/api/plans/act": "/api/act/plans?city=Chennai",
    "/api/plans/hathway": "/api/hathway/plans?city=Chennai",
  };

  return {
    plugins: [
      react(),
      {
        name: "local-broadband-proxy",

        configureServer(server) {
          server.middlewares.use(async (req, res, next) => {
            const pathname = new URL(
              req.url || "/",
              "http://localhost",
            ).pathname;

            if (!pathname.startsWith("/api/")) {
              next();
              return;
            }

            if (pathname.startsWith("/api/optimum/") || pathname === "/api/refresh") {
              try {
                let body = "";
                for await (const chunk of req) {
                  body += chunk.toString();
                  if (body.length > 4096) {
                    res.writeHead(413).end();
                    return;
                  }
                }
                const headers = new Headers();
                if (req.headers.origin) headers.set("Origin", req.headers.origin);
                const request = new Request(`http://${req.headers.host}${req.url}`, {
                  method: req.method, headers,
                  ...(req.method === "POST" ? { body } : {}),
                });
                const response = await optimumProxy(request, base, key);
                res.writeHead(response.status, Object.fromEntries(response.headers.entries()));
                res.end(await response.text());
              } catch {
                res.writeHead(502, { "Content-Type": "application/json" });
                res.end(JSON.stringify({ error: "Lookup proxy failed. Availability is unknown." }));
              }
              return;
            }

            const send = (status: number, body: unknown) => {
              res.statusCode = status;
              res.setHeader("Content-Type", "application/json");
              res.setHeader("Cache-Control", "no-store");
              res.end(JSON.stringify(body));
            };

            if (req.method !== "GET") {
              res.setHeader("Allow", "GET");
              send(405, { error: "Only GET is supported." });
              return;
            }

            if (!Object.hasOwn(routes, pathname)) {
              send(404, { error: "Unknown API endpoint." });
              return;
            }

            if (!base || !key) {
              send(503, {
                error: "Backend configuration is missing.",
                base_url_present: Boolean(base),
                api_key_present: Boolean(key),
              });
              return;
            }

            const started = performance.now();

            try {
              const root = new URL(base);

              if (
                !["http:", "https:"].includes(root.protocol) ||
                root.username ||
                root.password
              ) {
                send(503, { error: "Invalid backend URL." });
                return;
              }

              const response = await fetch(
                new URL(routes[pathname], root),
                {
                  headers: {
                    Accept: "application/json",
                    "X-API-Key": key,
                  },
                  redirect: "manual",
                  signal: AbortSignal.timeout(12000),
                },
              );

              if (!response.ok) {
                send(502, {
                  error: "Backend request failed.",
                  upstream_status: response.status,
                });
                return;
              }

              const payload = (await response.json()) as {
                provider?: string;
                data?: unknown;
              };

              if (!Array.isArray(payload.data)) {
                send(502, {
                  error: "Backend response must contain a data array.",
                });
                return;
              }

              const elapsed = performance.now() - started;

              res.setHeader(
                "Server-Timing",
                `backend;dur=${elapsed.toFixed(2)}`,
              );

              send(200, {
                provider: payload.provider,
                count: payload.data.length,
                data: payload.data,
              });
            } catch {
              send(502, {
                error:
                  "Could not retrieve plans. Check the Python API on port 8001.",
              });
            }
          });
        },
      },
    ],

    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
    },
  };
});
