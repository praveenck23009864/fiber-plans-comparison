import { env } from "cloudflare:workers";
import { optimumProxy } from "../../../../lib/optimum-proxy";

function handle(request: Request) {
  const bindings = env as unknown as { BROADBAND_API_BASE_URL?: string; BROADBAND_API_KEY?: string };
  return optimumProxy(request, bindings.BROADBAND_API_BASE_URL, bindings.BROADBAND_API_KEY);
}
export const GET = handle;
export const POST = handle;
export const DELETE = handle;
