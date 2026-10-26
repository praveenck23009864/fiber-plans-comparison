// Server-only module: never import from client components.
export async function optimumProxy(request: Request, base?: string, key?: string): Promise<Response> {
  const reply = (status: number, error: string) => Response.json({ error }, { status, headers: { "Cache-Control": "no-store" } });
  const url = new URL(request.url);
  const path = url.pathname;
  const refresh = path === "/api/refresh" && ["GET", "POST"].includes(request.method);
  const root = path === "/api/optimum/lookups";
  const item = /^\/api\/optimum\/lookups\/[0-9a-f-]{36}$/.test(path);
  const selection = /^\/api\/optimum\/lookups\/[0-9a-f-]{36}\/selection$/.test(path);
  if (!(refresh || (root && request.method === "POST") || (item && ["GET", "DELETE"].includes(request.method)) || (selection && request.method === "POST"))) return reply(404, "Unknown backend endpoint.");
  const origin = request.headers.get("Origin");
  if (request.method !== "GET" && origin && origin !== url.origin) return reply(403, "Cross-origin lookup requests are not allowed.");
  if (!base || !key) return reply(503, "The backend proxy is not configured.");
  try {
    const target = new URL(base);
    if (!["http:", "https:"].includes(target.protocol) || target.username || target.password) return reply(503, "Invalid backend URL.");
    const body = request.method === "POST" ? await request.text() : undefined;
    if (body && body.length > 4096) return reply(413, "Lookup input is too large.");
    const upstream = await fetch(new URL(path, target), {
      method: request.method, body, redirect: "manual", signal: AbortSignal.timeout(15000),
      headers: { "X-API-Key": key, "Content-Type": "application/json", Accept: "application/json" },
    });
    if (!upstream.ok) {
      if (refresh && upstream.status === 409) return reply(409, "A requested provider already has a queued or running refresh.");
      const messages: Record<number, string> = { 401: "The backend rejected its API key.", 403: "Backend access denied.", 404: "Lookup not found or expired. Start a new search.", 409: "Lookup already active, or address choice expired.", 422: "Check the street number, city, two-letter state, ZIP and unit.", 429: "Lookup capacity reached. Try again later." };
      return reply(upstream.status >= 500 ? 502 : upstream.status, messages[upstream.status] || "Backend lookup request failed.");
    }
    const payload = await upstream.json();
    return Response.json(payload, { status: upstream.status, headers: { "Cache-Control": "no-store" } });
  } catch {
    return reply(502, "Could not reach the lookup backend. Availability is unknown.");
  }
}
