import { env } from "cloudflare:workers";

const providers: Record<string, { name: string; path: string }> = {
  jio: {
    name: "JioFiber",
    path: "/api/jio/plans",
  },
  act: {
    name: "ACT Fibernet",
    path: "/api/act/plans?city=Chennai",
  },
  hathway: {
    name: "Hathway",
    path: "/api/hathway/plans?city=Chennai",
  },
};

const fields = [
  "provider",
  "plan_name",
  "price",
  "speed_mbps",
  "state",
  "city",
  "pincode",
  "offer",
  "offer_price",
  "offer_expiry",
  "source_url",
  "availability_verified",
  "record_id", "provider_plan_id", "last_checked", "last_seen_at", "updated_at",
  "currency", "billing_period", "pricing_conditions",
] as const;

const numericFields = new Set<string>([
  "price",
  "speed_mbps",
  "offer_price",
]);

const responseHeaders = {
  "Cache-Control": "no-store",
};

function failure(
  status: number,
  code: string,
  error: string,
  extra: Record<string, unknown> = {},
) {
  return Response.json(
    { error, code, ...extra },
    { status, headers: responseHeaders },
  );
}

export async function GET(
  _request: Request,
  context: { params: Promise<{ provider: string }> },
) {
  const { provider } = await context.params;

  if (!Object.hasOwn(providers, provider)) {
    return failure(404, "UNKNOWN_PROVIDER", "Unknown provider.");
  }

  const selected = providers[provider];

  const bindings = env as unknown as {
    BROADBAND_API_BASE_URL?: string;
    BROADBAND_API_KEY?: string;
  };

  const base = bindings.BROADBAND_API_BASE_URL?.trim();
  const key = bindings.BROADBAND_API_KEY?.trim();

  if (!base || !key) {
    return failure(
      503,
      "NOT_CONFIGURED",
      "The website proxy is missing its backend configuration.",
      {
        base_url_present: Boolean(base),
        api_key_present: Boolean(key),
      },
    );
  }

  let target: URL;

  try {
    const root = new URL(base);

    if (
      !["http:", "https:"].includes(root.protocol) ||
      root.username ||
      root.password
    ) {
      throw new Error("Invalid URL");
    }

    target = new URL(selected.path, root);
  } catch {
    return failure(
      503,
      "INVALID_BACKEND_URL",
      "BROADBAND_API_BASE_URL must be a valid HTTP or HTTPS URL.",
    );
  }

  let response: Response;
  let responseText: string;

  try {
    response = await fetch(target.toString(), {
      method: "GET",
      headers: {
        Accept: "application/json",
        "X-API-Key": key,
      },
      redirect: "manual",
      signal: AbortSignal.timeout(12000),
    });

    // Read the response body within the same timeout/error boundary.
    responseText = await response.text();
  } catch (error: unknown) {
    // Log only the error type. Never log credentials or request headers.
    const errorType =
      error instanceof Error ? error.name : "UnknownError";

    const safeMessage =
  error instanceof Error
    ? error.message
        .split(key).join("[REDACTED]")
        .replace(/https?:\/\/[^\s"'<>]+/g, "[URL hidden]")
    : "Unknown fetch error";

console.error(
  `[Broadband proxy] ${provider}: ${errorType}: ${safeMessage}`,
);

    const timedOut =
      errorType === "TimeoutError" || errorType === "AbortError";

    return failure(
      timedOut ? 504 : 502,
      timedOut ? "BACKEND_TIMEOUT" : "BACKEND_FETCH_FAILED",
      timedOut
        ? "The backend did not respond within 12 seconds."
        : "The website runtime could not fetch the backend response.",
      { error_type: errorType },
    );
  }

  if (!response.ok) {
    return failure(
      502,
      "BACKEND_HTTP_ERROR",
      response.status === 401 || response.status === 403
        ? "The backend rejected the API key."
        : "The backend returned an error.",
      { upstream_status: response.status },
    );
  }

  let payload: unknown;

  try {
    payload = JSON.parse(responseText);
  } catch {
    return failure(
      502,
      "BACKEND_INVALID_JSON",
      "The backend response was not valid JSON.",
    );
  }

  if (
    !payload ||
    typeof payload !== "object" ||
    !("data" in payload) ||
    !Array.isArray(payload.data)
  ) {
    return failure(
      502,
      "BACKEND_INVALID_FORMAT",
      "Expected a backend response containing a data array.",
    );
  }

  const plans: Record<string, unknown>[] = [];

  for (const item of payload.data) {
    if (
      !item ||
      typeof item !== "object" ||
      Array.isArray(item)
    ) {
      return failure(
        502,
        "BACKEND_INVALID_PLAN",
        "A backend plan record is not an object.",
      );
    }

    const row = item as Record<string, unknown>;

    if (
      typeof row.provider !== "string" ||
      !row.provider.trim()
    ) {
      return failure(
        502,
        "BACKEND_INVALID_PLAN",
        "A backend plan is missing its provider.",
      );
    }

    const clean: Record<string, unknown> = {};

    for (const field of fields) {
      const value = row[field];

      if (numericFields.has(field)) {
        clean[field] =
          typeof value === "number" && Number.isFinite(value)
            ? value
            : null;
      } else if (field === "availability_verified") {
        clean[field] = value === true;
      } else {
        clean[field] =
          typeof value === "string" && value.trim()
            ? value.trim()
            : null;
      }
    }

    plans.push(clean);
  }

  return Response.json(
    {
      provider: selected.name,
      count: plans.length,
      data: plans,
    },
    { headers: responseHeaders },
  );
}
