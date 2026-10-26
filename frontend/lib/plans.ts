export type Plan = {
  provider: string;
  plan_name: string | null;
  price: number | null;
  speed_mbps: number | null;
  state: string | null;
  city: string | null;
  pincode: string | null;
  offer: string | null;
  offer_price: number | null;
  offer_expiry: string | null;
  source_url: string | null;
  availability_verified: boolean;
  key: string;
  legacy_key?: string;

  record_id?: string | null;
  provider_plan_id?: string | null;
  category?: string | null;
  last_checked?: string | null;

  price_basis?: string | null;
  billing_period?: string | null;
  validity_text?: string | null;
  base_validity_days?: number | null;
  bonus_validity_days?: number | null;
  validity_days?: number | null;
  duration_options_text?: string | null;

  tax_status?: string | null;
  tax_note?: string | null;
  tax_note_scope?: string | null;
  total_payable?: number | null;

  data_allowance_text?: string | null;
  fup_limit_text?: string | null;
  post_fup_speed_text?: string | null;
  calling_benefits_text?: string | null;
  talktime_text?: string | null;
  isd_talktime_text?: string | null;
  tv_benefits_text?: string | null;
  long_term_benefit_text?: string | null;

  installation_terms?: string | null;
  installation_charge?: number | null;
  router_charge?: number | null;
  security_deposit?: number | null;

  subscriptions?: string[];
  subscription_terms?: string | null;
  ott_labels_shown?: string[];
  benefits?: string[];
  notes?: string[];
  description?: string | null;

  details_complete?: boolean;
  source_detail_fields?: Record<string, unknown>;
};

export const providers = [
  { id: "jio", name: "JioFiber", short: "Jio", color: "#2257ce" },
  { id: "act", name: "ACT Fibernet", short: "ACT", color: "#c63748" },
  { id: "hathway", name: "Hathway", short: "H", color: "#6a49ba" },
];

export const money = (value: number | null | undefined) =>
  typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat("en-IN", {
        style: "currency",
        currency: "INR",
        maximumFractionDigits: 2,
      }).format(value)
    : "Not provided";

export const hasSpeed = (plan: Plan) =>
  typeof plan.speed_mbps === "number" &&
  Number.isFinite(plan.speed_mbps) &&
  plan.speed_mbps > 0;

export function safeSource(url: string | null) {
  try {
    const parsed = new URL(url || "");
    return ["https:", "http:"].includes(parsed.protocol)
      ? parsed.href
      : null;
  } catch {
    return null;
  }
}

export function displayText(value: unknown): string {
  return typeof value === "string" && value.trim()
    ? value.trim()
    : "Not provided";
}

export function displayList(value: unknown): string {
  if (!Array.isArray(value)) return "Not provided";

  const items = value.filter(
    (item): item is string =>
      typeof item === "string" && Boolean(item.trim()),
  );

  return items.length ? items.join(" · ") : "Not provided";
}

export function validityLabel(plan: Plan): string {
  // Preserve the source wording, including extra-day conditions.
  if (plan.validity_text?.trim()) return plan.validity_text;

  return typeof plan.validity_days === "number"
    ? `${plan.validity_days} days`
    : "Not provided";
}

export function taxLabel(plan: Plan): string {
  if (plan.tax_status === "included") return "Tax included";
  if (plan.tax_status === "excluded") return "Tax extra";
  return "Tax status not verified";
}

export function checkedLabel(value: string | null | undefined): string {
  if (!value) return "Not provided";

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not provided";

  return (
    new Intl.DateTimeFormat("en-IN", {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: "Asia/Kolkata",
    }).format(date) + " IST"
  );
}

export function identify(
  plans: Omit<Plan, "key">[],
  provider: string,
): Plan[] {
  const counts = new Map<string, number>();

  return plans.map((plan) => {
    // Migrate previous content keys while retaining selections across price changes.
    const content = JSON.stringify([
      provider,
      plan.plan_name,
      plan.price,
      plan.speed_mbps,
      plan.state,
      plan.city,
      plan.pincode,
      plan.offer,
      plan.offer_price,
      plan.offer_expiry,
      plan.source_url,
    ]);

    const occurrence = counts.get(content) || 0;
    counts.set(content, occurrence + 1);

    return {
      ...plan,
      legacy_key: `${content}#${occurrence}`,
      key: plan.record_id ? `${provider}:${plan.record_id}` : `${content}#${occurrence}`,
    };
  });
}

export function matches(anchor: Plan, plans: Plan[]): Plan[] {
  if (!hasSpeed(anchor)) return [];

  return plans
    .filter(
      (plan) =>
        plan.key !== anchor.key &&
        plan.provider !== anchor.provider &&
        hasSpeed(plan),
    )
    .sort(
      (a, b) =>
        Math.abs(a.speed_mbps! - anchor.speed_mbps!) -
        Math.abs(b.speed_mbps! - anchor.speed_mbps!),
    )
    .slice(0, 6);
}
