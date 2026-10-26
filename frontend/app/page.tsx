"use client";

import OptimumSearch from "../components/optimum-search";
import CatalogRefresh from "../components/catalog-refresh";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  providers,
  identify,
  money,
  safeSource,
  type Plan,
} from "../lib/plans";

type DetailedPlan = Plan & {
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
  source_detail_fields?: Record<string, unknown>;
};

type ProviderState = {
  loading: boolean;
  loaded: boolean;
  plans: DetailedPlan[];
  error: string | null;
};

type View = "browse" | "compare" | "about";
type PlanType = "broadband" | "vouchers" | "all";

type ComparisonRow = {
  label: string;
  fields: (keyof DetailedPlan)[];
  render: (plan: DetailedPlan) => ReactNode;
  always?: boolean;
};

const STORAGE_KEY = "broadband-selection-v1";

function text(value: unknown): string {
  return typeof value === "string" && value.trim()
    ? value.trim()
    : "Not provided";
}

function number(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function hasSpeed(plan: DetailedPlan): boolean {
  return number(plan.speed_mbps) && plan.speed_mbps > 0;
}

function list(value: unknown): string[] {
  if (!Array.isArray(value)) return [];

  const seen = new Set<string>();

  return value.filter((item): item is string => {
    if (typeof item !== "string" || !item.trim()) return false;

    const key = item.trim().toLowerCase();
    if (seen.has(key)) return false;

    seen.add(key);
    return true;
  }).map((item) => item.trim());
}

function hasValue(value: unknown): boolean {
  if (number(value)) return true;
  if (typeof value === "string") return Boolean(value.trim());
  if (Array.isArray(value)) return value.some(hasValue);
  return false;
}

function additionalBenefits(plan: DetailedPlan): string[] {
  const shown = new Set(
    [
      ...list(plan.subscriptions),
      ...list(plan.ott_labels_shown),
    ].map((value) => value.toLowerCase()),
  );

  return list(plan.benefits).filter(
    (value) => !shown.has(value.toLowerCase()),
  );
}

function days(value: unknown): string {
  return number(value) ? `${value} days` : "Not provided";
}

function validity(plan: DetailedPlan): string {
  if (plan.validity_text?.trim()) return plan.validity_text;
  return days(plan.validity_days);
}

function taxLabel(plan: DetailedPlan): string {
  if (plan.tax_status === "included") return "Tax included";
  if (plan.tax_status === "excluded") return "Tax extra";
  return "Tax status not verified";
}

function checkedLabel(value: unknown): string {
  if (typeof value !== "string" || !value) return "Not provided";

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

function DetailList({ values }: { values: unknown }) {
  const items = list(values);

  if (!items.length) return <>Not provided</>;

  return (
    <ul className="bc-detail-list">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

function Source({ plan }: { plan: DetailedPlan }) {
  const url = safeSource(plan.source_url);

  if (!url) return <span>Not provided</span>;

  return (
    <a href={url} target="_blank" rel="noopener noreferrer">
      View source
      <span className="bc-sr-only">
        {" "}for {plan.plan_name || plan.provider}, opens a new tab
      </span>
    </a>
  );
}

const comparisonRows: ComparisonRow[] = [
  {
    label: "Advertised price",
    fields: ["price"],
    always: true,
    render: (p) => money(p.price),
  },
  {
    label: "Price basis",
    fields: ["price_basis"],
    render: (p) => text(p.price_basis),
  },
  {
    label: "Billing period",
    fields: ["billing_period"],
    render: (p) => text(p.billing_period),
  },
  {
    label: "Validity",
    fields: ["validity_text", "validity_days"],
    render: validity,
  },
  {
    label: "Base validity",
    fields: ["base_validity_days"],
    render: (p) => days(p.base_validity_days),
  },
  {
    label: "Extra validity",
    fields: ["bonus_validity_days"],
    render: (p) => days(p.bonus_validity_days),
  },
  {
    label: "Total validity",
    fields: ["validity_days"],
    render: (p) => days(p.validity_days),
  },
  {
    label: "Duration options",
    fields: ["duration_options_text"],
    render: (p) => text(p.duration_options_text),
  },
  {
    label: "Tax status",
    fields: ["tax_status"],
    always: true,
    render: taxLabel,
  },
  {
    label: "Tax note",
    fields: ["tax_note"],
    render: (p) => (
      <>
        {text(p.tax_note)}
        {p.tax_note && p.tax_note_scope === "page" && (
          <small className="bc-muted">
            {" "}— General page note; confirm applicability.
          </small>
        )}
      </>
    ),
  },
  {
    label: "Total payable",
    fields: ["total_payable"],
    render: (p) => money(p.total_payable),
  },
  {
    label: "Speed",
    fields: ["speed_mbps"],
    always: true,
    render: (p) =>
      hasSpeed(p) ? `${p.speed_mbps} Mbps` : "Not provided",
  },
  {
    label: "Data allowance",
    fields: ["data_allowance_text"],
    render: (p) => text(p.data_allowance_text),
  },
  {
    label: "FUP limit",
    fields: ["fup_limit_text"],
    render: (p) => text(p.fup_limit_text),
  },
  {
    label: "Speed after FUP",
    fields: ["post_fup_speed_text"],
    render: (p) => text(p.post_fup_speed_text),
  },
  {
    label: "Calling benefits",
    fields: ["calling_benefits_text"],
    render: (p) => text(p.calling_benefits_text),
  },
  {
    label: "Talktime",
    fields: ["talktime_text"],
    render: (p) => text(p.talktime_text),
  },
  {
    label: "ISD talktime",
    fields: ["isd_talktime_text"],
    render: (p) => text(p.isd_talktime_text),
  },
  {
    label: "TV benefits",
    fields: ["tv_benefits_text"],
    render: (p) => text(p.tv_benefits_text),
  },
  {
    label: "Subscriptions",
    fields: ["subscriptions"],
    render: (p) => <DetailList values={p.subscriptions} />,
  },
  {
    label: "OTT labels shown on source",
    fields: ["ott_labels_shown"],
    render: (p) => <DetailList values={p.ott_labels_shown} />,
  },
  {
    label: "Subscription terms",
    fields: ["subscription_terms"],
    render: (p) => text(p.subscription_terms),
  },
  {
    label: "Other benefits / offers",
    fields: ["benefits"],
    render: (p) => <DetailList values={additionalBenefits(p)} />,
  },
  {
    label: "Long-term benefit",
    fields: ["long_term_benefit_text"],
    render: (p) => text(p.long_term_benefit_text),
  },
  {
    label: "Description",
    fields: ["description"],
    render: (p) => text(p.description),
  },
  {
    label: "Notes and conditions",
    fields: ["notes"],
    render: (p) => <DetailList values={p.notes} />,
  },
  {
    label: "Installation terms",
    fields: ["installation_terms"],
    render: (p) => text(p.installation_terms),
  },
  {
    label: "Installation charge",
    fields: ["installation_charge"],
    render: (p) => money(p.installation_charge),
  },
  {
    label: "Router charge",
    fields: ["router_charge"],
    render: (p) => money(p.router_charge),
  },
  {
    label: "Security deposit",
    fields: ["security_deposit"],
    render: (p) => money(p.security_deposit),
  },
  {
    label: "City",
    fields: ["city"],
    always: true,
    render: (p) => p.city?.trim() || "Location not specified",
  },
  {
    label: "State",
    fields: ["state"],
    render: (p) => text(p.state),
  },
  {
    label: "PIN code",
    fields: ["pincode"],
    render: (p) => text(p.pincode),
  },
  {
    label: "Category",
    fields: ["category"],
    render: (p) => text(p.category),
  },
  {
    label: "Offer",
    fields: ["offer"],
    render: (p) => text(p.offer),
  },
  {
    label: "Offer price",
    fields: ["offer_price"],
    render: (p) => money(p.offer_price),
  },
  {
    label: "Offer expiry",
    fields: ["offer_expiry"],
    render: (p) => text(p.offer_expiry),
  },
  {
    label: "Availability",
    fields: ["availability_verified"],
    always: true,
    render: (p) =>
      p.availability_verified
        ? "Availability verified in saved data"
        : "Availability not verified",
  },
  {
    label: "Last checked",
    fields: ["last_checked"],
    render: (p) => checkedLabel(p.last_checked),
  },
  {
    label: "Source",
    fields: ["source_url"],
    always: true,
    render: (p) => <Source plan={p} />,
  },
];

function rowHasData(row: ComparisonRow, plan: DetailedPlan): boolean {
  if (row.label === "Other benefits / offers") {
    return additionalBenefits(plan).length > 0;
  }

  return row.fields.some((field) => hasValue(plan[field]));
}

function PlanDetails({ plan }: { plan: DetailedPlan }) {
  const rows = comparisonRows.filter(
    (row) => row.always || rowHasData(row, plan),
  );

  const rawFields = Object.entries(
    plan.source_detail_fields || {},
  ).filter(([, value]) => hasValue(value));

  return (
    <details className="bc-details">
      <summary>View saved plan details</summary>

      <dl>
        {rows.map((row) => (
          <div key={row.label}>
            <dt>{row.label}</dt>
            <dd>{row.render(plan)}</dd>
          </div>
        ))}
      </dl>

      {rawFields.length > 0 && (
        <details>
          <summary>Original source fields</summary>
          <dl>
            {rawFields.map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>
                  {Array.isArray(value)
                    ? <DetailList values={value} />
                    : typeof value === "string" || number(value)
                      ? String(value)
                      : "Not provided"}
                </dd>
              </div>
            ))}
          </dl>
        </details>
      )}

      <p className="bc-muted">
        Fields without saved information are omitted here.
        Missing charges do not mean free service.
      </p>
    </details>
  );
}

function initialStates(): Record<string, ProviderState> {
  return Object.fromEntries(
    providers.map((provider) => [
      provider.id,
      {
        loading: true,
        loaded: false,
        plans: [],
        error: null,
      },
    ]),
  );
}

export default function Home() {
  const [states, setStates] = useState(initialStates);
  const [view, setView] = useState<View>("browse");
  const [providerFilter, setProviderFilter] = useState("");
  const [cityFilter, setCityFilter] = useState("");
  const [minPrice, setMinPrice] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [minSpeed, setMinSpeed] = useState("");
  const [query, setQuery] = useState("");
  const [planType, setPlanType] = useState<PlanType>("broadband");
  const [sort, setSort] = useState("provider");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [selection, setSelection] = useState<string[]>([]);
  const [restored, setRestored] = useState(false);
  const [showAllFields, setShowAllFields] = useState(false);
  const [anchorKey, setAnchorKey] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  const loadProvider = useCallback(async (id: string) => {
    setStates((current) => ({
      ...current,
      [id]: {
        ...current[id],
        loading: true,
        error: null,
      },
    }));

    try {
      const response = await fetch(`/api/plans/${id}`, {
        cache: "no-store",
        signal: AbortSignal.timeout(20000),
      });

      const payload = (await response.json()) as {
        error?: string;
        data?: unknown;
      };

      if (!response.ok) {
        throw new Error(payload.error || "Unable to load this provider.");
      }

      if (!Array.isArray(payload.data)) {
        throw new Error("The API returned an invalid plan list.");
      }

      const expected = providers.find((provider) => provider.id === id);

      if (!expected) throw new Error("Unknown provider.");

      const valid = payload.data.every(
        (row: unknown) =>
          row !== null &&
          typeof row === "object" &&
          !Array.isArray(row) &&
          (row as Record<string, unknown>).provider === expected.name,
      );

      if (!valid) {
        throw new Error("The API returned unexpected plan data.");
      }

      // identify adds UI keys and preserves all returned fields.
      const plans = identify(
        payload.data as Omit<Plan, "key">[],
        expected.name,
      ) as DetailedPlan[];

      setSelection(current => current.map(key => plans.find(plan => plan.legacy_key === key)?.key || key));

      setStates((current) => ({
        ...current,
        [id]: {
          loading: false,
          loaded: true,
          plans,
          error: null,
        },
      }));
    } catch (error) {
      setStates((current) => ({
        ...current,
        [id]: {
          ...current[id],
          loading: false,
          error:
            error instanceof Error
              ? error.message
              : "Unable to load this provider.",
        },
      }));
    }
  }, []);

  useEffect(() => {
    for (const provider of providers) {
      void loadProvider(provider.id);
    }

    try {
      const saved: unknown = JSON.parse(
        localStorage.getItem(STORAGE_KEY) || "[]",
      );

      if (Array.isArray(saved)) {
        setSelection(
          [...new Set(
            saved.filter((value): value is string =>
              typeof value === "string",
            ),
          )].slice(0, 3),
        );
      }
    } catch {
      // Invalid saved selections do not prevent rendering.
    }

    setRestored(true);
  }, [loadProvider]);

  useEffect(() => {
    if (!restored) return;

    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(selection));
    } catch {
      // Comparison still works if browser storage is unavailable.
    }
  }, [selection, restored]);

  const allPlans = useMemo(
    () => providers.flatMap(
      (provider) => states[provider.id]?.plans || [],
    ),
    [states],
  );

  // Define selected BEFORE any comparison calculations.
  const selected = useMemo(
    () => selection
      .map((key) => allPlans.find((plan) => plan.key === key))
      .filter((plan): plan is DetailedPlan => Boolean(plan)),
    [selection, allPlans],
  );

  const loading = providers.some(
    (provider) => states[provider.id]?.loading,
  );

  const loadedProviders = providers.filter(
    (provider) => states[provider.id]?.loaded,
  ).length;

  const cities = useMemo(
    () => [...new Set(
      allPlans
        .map((plan) => plan.city?.trim())
        .filter((city): city is string => Boolean(city)),
    )].sort(),
    [allPlans],
  );

  const invalidRange =
    minPrice !== "" &&
    maxPrice !== "" &&
    Number(minPrice) > Number(maxPrice);

  const visiblePlans = useMemo(() => {
    const search = query.trim().toLowerCase();

    const filtered = allPlans.filter((plan) => {
      if (invalidRange) return false;
      if (providerFilter && plan.provider !== providerFilter) return false;

      if (cityFilter === "__unknown__") {
        if (plan.city?.trim()) return false;
      } else if (cityFilter && plan.city !== cityFilter) {
        return false;
      }

      if (planType === "broadband" && !hasSpeed(plan)) return false;
      if (planType === "vouchers" && hasSpeed(plan)) return false;

      if (
        search &&
        !`${plan.provider} ${plan.plan_name || ""} ${plan.category || ""}`
          .toLowerCase()
          .includes(search)
      ) return false;

      if (
        minPrice !== "" &&
        (!number(plan.price) || plan.price < Number(minPrice))
      ) return false;

      if (
        maxPrice !== "" &&
        (!number(plan.price) || plan.price > Number(maxPrice))
      ) return false;

      if (
        minSpeed !== "" &&
        (!hasSpeed(plan) || plan.speed_mbps! < Number(minSpeed))
      ) return false;

      return true;
    });

    return filtered.sort((a, b) => {
      if (sort === "speed") {
        return (b.speed_mbps || 0) - (a.speed_mbps || 0);
      }

      if (sort === "price") {
        return (
          (number(a.price) ? a.price : Infinity) -
          (number(b.price) ? b.price : Infinity)
        );
      }

      return (
        a.provider.localeCompare(b.provider) ||
        (a.plan_name || "").localeCompare(b.plan_name || "")
      );
    });
  }, [
    allPlans,
    providerFilter,
    cityFilter,
    planType,
    query,
    minPrice,
    maxPrice,
    minSpeed,
    sort,
    invalidRange,
  ]);

  const anchor = allPlans.find((plan) => plan.key === anchorKey);

  const suggestions = useMemo(() => {
    if (!anchor || !hasSpeed(anchor)) return [];

    return allPlans
      .filter(
        (plan) =>
          plan.provider !== anchor.provider &&
          hasSpeed(plan) &&
          (
            !anchor.city?.trim() ||
            !plan.city?.trim() ||
            anchor.city.trim().toLowerCase() ===
              plan.city.trim().toLowerCase()
          ),
      )
      .sort(
        (a, b) =>
          Math.abs(a.speed_mbps! - anchor.speed_mbps!) -
          Math.abs(b.speed_mbps! - anchor.speed_mbps!),
      )
      .slice(0, 6);
  }, [anchor, allPlans]);

  const eligible = selected.filter(hasSpeed);
  const crossProvider =
    new Set(eligible.map((plan) => plan.provider)).size >= 2;

  const sameSpeed =
    eligible.length >= 2 &&
    eligible.every(
      (plan) => plan.speed_mbps === eligible[0].speed_mbps,
    );

  const visibleComparisonRows = comparisonRows.filter((row) => {
    if (showAllFields || row.always) return true;
    return selected.some((plan) => rowHasData(row, plan));
  });

  function navigate(next: View) {
    setView(next);
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function togglePlan(plan: DetailedPlan) {
    if (selection.includes(plan.key)) {
      setSelection((current) => current.filter((key) => key !== plan.key));
      setMessage("Plan removed from comparison.");
      return;
    }

    if (selection.length >= 3) {
      setMessage("You can compare up to three plans. Remove one first.");
      return;
    }

    setSelection((current) => [...current, plan.key]);
    setMessage("Plan added to comparison.");
  }

  function resetFilters() {
    setProviderFilter("");
    setCityFilter("");
    setMinPrice("");
    setMaxPrice("");
    setMinSpeed("");
    setQuery("");
    setPlanType("broadband");
    setAnchorKey(null);
  }

  function renderCard(plan: DetailedPlan) {
    const brand = providers.find(
      (provider) => provider.name === plan.provider,
    );
    const isSelected = selection.includes(plan.key);

    return (
      <article className="bc-card" key={plan.key}>
        <div className="bc-card-top">
          <span
            className="bc-brand"
            style={{ color: brand?.color || "#2257ce" }}
          >
            {plan.provider}
          </span>
          {plan.category && (
            <span className="bc-badge">{plan.category}</span>
          )}
        </div>

        <h3>{text(plan.plan_name)}</h3>
        <p className="bc-price">{money(plan.price)}</p>
        <p className="bc-muted">Advertised price</p>

        <div className="bc-card-facts">
          <strong>
            {hasSpeed(plan) ? `${plan.speed_mbps} Mbps` : "Speed not provided"}
          </strong>
          <span>{plan.city?.trim() || "Location not specified"}</span>
          <span>Validity: {validity(plan)}</span>
          <span>Price basis: {text(plan.price_basis)}</span>
          <span>{taxLabel(plan)}</span>
        </div>

        {plan.offer && <p className="bc-offer">{plan.offer}</p>}
        {number(plan.offer_price) && (
          <p>Offer price: {money(plan.offer_price)}</p>
        )}

        <p className="bc-muted">
          {plan.availability_verified
            ? "Availability verified in saved data"
            : "Availability not verified"}
        </p>

        <PlanDetails plan={plan} />

        <div className="bc-card-actions">
          <button
            type="button"
            className={isSelected ? "bc-secondary" : "bc-primary"}
            aria-pressed={isSelected}
            onClick={() => togglePlan(plan)}
          >
            {isSelected ? "Remove from compare" : "Add to compare"}
          </button>
          <Source plan={plan} />
        </div>

        {hasSpeed(plan) && (
          <button
            type="button"
            className="bc-link-button"
            onClick={() => setAnchorKey(plan.key)}
          >
            Find similar speeds from other providers
          </button>
        )}
      </article>
    );
  }

  return (
    <div className="bc-app">
      <style>{`
        .bc-app {
          min-height: 100vh;
          background: #f5f7fb;
          color: #172b49;
          font-family: Arial, sans-serif;
          line-height: 1.55;
          padding-bottom: 150px;
        }
        .bc-app * { box-sizing: border-box; }
        .bc-app a { color: #174eaf; text-decoration: underline; }
        .bc-app button, .bc-app input, .bc-app select {
          font: inherit;
        }
        .bc-app button { cursor: pointer; }
        .bc-app button:disabled { opacity: .55; cursor: not-allowed; }
        .bc-app :focus-visible {
          outline: 3px solid #2257ce;
          outline-offset: 3px;
        }
        .bc-header {
          background: white;
          border-bottom: 1px solid #dce3ed;
          padding: 18px max(20px, calc((100vw - 1280px) / 2));
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 20px;
          flex-wrap: wrap;
        }
        .bc-logo {
          border: 0; background: none; color: #172b49;
          font-size: 22px !important; font-weight: 800;
          padding: 0;
        }
        .bc-header nav { display: flex; gap: 8px; flex-wrap: wrap; }
        .bc-nav-button, .bc-secondary {
          border: 1px solid #ccd6e4;
          border-radius: 8px;
          padding: 9px 14px;
          background: white;
          color: #172b49;
        }
        .bc-nav-button[aria-current="page"] {
          background: #eaf0ff; color: #174eaf; border-color: #a8bfee;
        }
        .bc-main { max-width: 1280px; margin: auto; padding: 28px 20px; }
        .bc-hero { padding: 22px 0 28px; }
        .bc-hero h1 {
          font-size: clamp(28px, 4vw, 42px);
          line-height: 1.15; margin: 10px 0 14px;
        }
        .bc-eyebrow {
          color: #174eaf; font-size: 12px; font-weight: 700;
          letter-spacing: 1.5px;
        }
        .bc-muted { color: #52647b; font-size: 14px; }
        .bc-stats {
          display: grid; grid-template-columns: repeat(3, 1fr);
          gap: 16px; margin-bottom: 24px;
        }
        .bc-stat, .bc-panel, .bc-card {
          background: white; border: 1px solid #dce3ed;
          border-radius: 12px; padding: 22px;
        }
        .bc-stat strong { display: block; font-size: 28px; }
        .bc-layout {
          display: grid; grid-template-columns: 240px minmax(0, 1fr);
          gap: 24px; align-items: start;
        }
        .bc-filters label {
          display: grid; gap: 5px; margin-bottom: 16px;
          font-size: 14px; font-weight: 600;
        }
        .bc-app input:not([type="checkbox"]), .bc-app select {
          width: 100%; min-height: 42px; border: 1px solid #bcc9da;
          border-radius: 7px; padding: 8px 10px;
          color: #172b49; background: white;
        }
        .bc-toolbar {
          display: flex; align-items: end; gap: 12px;
          margin-bottom: 16px; flex-wrap: wrap;
        }
        .bc-toolbar label { flex: 1; min-width: 150px; font-size: 14px; }
        .bc-grid {
          display: grid; grid-template-columns: repeat(2, minmax(0,1fr));
          gap: 18px;
        }
        .bc-card { min-width: 0; }
        .bc-card h3 { margin: 14px 0 8px; font-size: 20px; }
        .bc-card-top, .bc-card-actions {
          display: flex; align-items: center;
          justify-content: space-between; gap: 10px; flex-wrap: wrap;
        }
        .bc-brand { font-size: 14px; font-weight: 800; }
        .bc-badge { background: #eef2f8; padding: 3px 8px; border-radius: 6px; font-size: 12px; }
        .bc-price { font-size: 30px; font-weight: 800; margin: 12px 0 0; }
        .bc-price + p { margin-top: 0; }
        .bc-card-facts { display: grid; gap: 6px; font-size: 14px; margin: 16px 0; }
        .bc-primary {
          border: 1px solid #2257ce; border-radius: 8px;
          padding: 10px 15px; color: white; background: #2257ce;
        }
        .bc-link-button {
          border: 0; background: none; color: #174eaf;
          padding: 12px 0 0; text-align: left; text-decoration: underline;
        }
        .bc-notice {
          padding: 14px 18px; background: #fff8e8;
          border: 1px solid #e9ce8c; border-radius: 9px;
          color: #624a13; margin-bottom: 20px;
        }
        .bc-error {
          background: #fff1f2; border: 1px solid #e8b0b8;
          color: #7a2331; border-radius: 9px; padding: 16px;
          margin-bottom: 16px;
        }
        .bc-error button { margin-left: 12px; }
        .bc-details { border-top: 1px solid #e2e8f0; margin: 18px 0; padding-top: 12px; }
        .bc-details summary { cursor: pointer; color: #174eaf; font-weight: 600; }
        .bc-details dl > div {
          padding: 8px 0; border-bottom: 1px solid #edf1f6;
          font-size: 14px; overflow-wrap: anywhere;
        }
        .bc-details dt { font-weight: 600; }
        .bc-details dd { margin: 3px 0 0; color: #52647b; }
        .bc-detail-list { margin: 0; padding-left: 18px; }
        .bc-table-scroll { overflow-x: auto; margin-top: 18px; }
        .bc-table { width: 100%; border-collapse: collapse; background: white; }
        .bc-table th, .bc-table td {
          padding: 15px; text-align: left; vertical-align: top;
          border: 1px solid #dce3ed; min-width: 200px;
          max-width: 340px; overflow-wrap: anywhere;
        }
        .bc-table thead th { background: #eef3fc; }
        .bc-table tbody th { font-size: 14px; }
        .bc-table button { display: block; margin-top: 10px; }
        .bc-tray {
          position: fixed; bottom: 0; left: 0; right: 0;
          z-index: 10; background: white; border-top: 1px solid #ccd6e4;
          box-shadow: 0 -4px 18px #172b4910; padding: 14px 20px;
        }
        .bc-tray-inner {
          max-width: 1240px; margin: auto; display: flex;
          align-items: center; gap: 14px; flex-wrap: wrap;
        }
        .bc-tray-items { display: flex; flex: 1; gap: 8px; flex-wrap: wrap; }
        .bc-chip { background: #eef3fc; border-radius: 7px; padding: 6px 10px; font-size: 13px; }
        .bc-chip button { border: 0; background: transparent; color: #174eaf; margin-left: 7px; }
        .bc-suggestions { margin: 20px 0; }
        .bc-suggestion {
          display: flex; gap: 12px; justify-content: space-between;
          align-items: center; border-top: 1px solid #dce3ed; padding: 12px 0;
        }
        .bc-empty { text-align: center; padding: 35px 20px; }
        .bc-skeleton {
          min-height: 280px; background: #e6ecf4;
          border: 1px solid #dce3ed; border-radius: 12px;
        }
        .bc-filter-toggle { display: none; }
        .bc-check { display: flex; align-items: center; gap: 10px; }
        .bc-check input { width: 18px; height: 18px; }
        .bc-status { color: #174eaf; margin: 12px 0; }
        .bc-sr-only {
          position: absolute; width: 1px; height: 1px; padding: 0;
          overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap;
        }
        @media (max-width: 850px) {
          .bc-layout { grid-template-columns: 1fr; }
          .bc-filter-toggle { display: block; margin-bottom: 15px; }
          .bc-filters { display: none; }
          .bc-filters.bc-open { display: block; }
        }
        @media (max-width: 580px) {
          .bc-grid { grid-template-columns: 1fr; }
          .bc-stats { gap: 8px; }
          .bc-stat { padding: 12px; font-size: 12px; }
          .bc-stat strong { font-size: 23px; }
          .bc-header { padding: 16px; }
          .bc-main { padding: 20px 14px; }
          .bc-tray-items { display: none; }
          .bc-app { padding-bottom: 130px; }
        }
      `}</style>

      <header className="bc-header">
        <button
          type="button"
          className="bc-logo"
          onClick={() => navigate("browse")}
        >
          BroadbandCompare
        </button>

        <nav aria-label="Main navigation">
          {([
            ["browse", "Browse plans"],
            ["compare", `Compare (${selected.length})`],
            ["about", "About the data"],
          ] as const).map(([id, label]) => (
            <button
              key={id}
              type="button"
              className="bc-nav-button"
              aria-current={view === id ? "page" : undefined}
              onClick={() => navigate(id)}
            >
              {label}
            </button>
          ))}
        </nav>
      </header>

      <main className="bc-main">
        <section className="bc-hero">
          <div className="bc-eyebrow">A CLEARER CONNECTION</div>
          <h1>
            {view === "compare"
              ? "A closer look, side by side."
              : view === "about"
                ? "Know what you’re comparing."
                : "Find a broadband plan that fits your needs."}
          </h1>
          <p className="bc-muted">
            Chennai &amp; general catalogs · JioFiber, ACT Fibernet and Hathway
          </p>
        </section>

        <div role="status" aria-live="polite" className="bc-status">
          {message}
        </div>

        {providers.map((provider) => {
          const state = states[provider.id];
          if (!state?.error) return null;

          return (
            <div className="bc-error" role="alert" key={provider.id}>
              <strong>{provider.name}:</strong> {state.error}
              {state.plans.length > 0 && (
                <span> Previously loaded plans remain visible.</span>
              )}
              <button
                type="button"
                className="bc-secondary"
                disabled={state.loading}
                onClick={() => void loadProvider(provider.id)}
              >
                {state.loading ? "Retrying…" : "Retry"}
              </button>
            </div>
          );
        })}

        {view === "browse" && (
          <>
            <section className="bc-stats" aria-label="Catalog summary">
              <div className="bc-stat">
                <strong>{allPlans.length}</strong>
                Saved plans loaded
              </div>
              <div className="bc-stat">
                <strong>{loadedProviders} / 3</strong>
                Providers loaded
              </div>
              <div className="bc-stat">
                <strong>{allPlans.filter(hasSpeed).length}</strong>
                Plans with known speeds
              </div>
            </section>

            <CatalogRefresh onUpdated={loadProvider} />

            <button
              type="button"
              className="bc-secondary bc-filter-toggle"
              aria-expanded={filtersOpen}
              aria-controls="plan-filters"
              onClick={() => setFiltersOpen((value) => !value)}
            >
              {filtersOpen ? "Hide filters" : "Show filters"}
            </button>

            <div className="bc-layout">
              <aside
                id="plan-filters"
                className={`bc-panel bc-filters ${filtersOpen ? "bc-open" : ""}`}
                aria-label="Plan filters"
              >
                <h2>Filters</h2>

                <label>
                  Provider
                  <select
                    value={providerFilter}
                    aria-label="Provider"
                    onChange={(event) => setProviderFilter(event.target.value)}
                  >
                    <option value="">All providers</option>
                    <option value="Optimum">Optimum · US address lookup</option>
                    {providers.map((provider) => (
                      <option key={provider.id} value={provider.name}>
                        {provider.name}
                      </option>
                    ))}
                  </select>
                </label>

                <label>
                  City
                  <select
                    value={cityFilter}
                    onChange={(event) => setCityFilter(event.target.value)}
                  >
                    <option value="">All locations</option>
                    {cities.map((city) => (
                      <option key={city} value={city}>{city}</option>
                    ))}
                    <option value="__unknown__">Location not specified</option>
                  </select>
                </label>

                <label>
                  Minimum advertised price (₹)
                  <input
                    type="number"
                    min="0"
                    value={minPrice}
                    onChange={(event) => setMinPrice(event.target.value)}
                  />
                </label>

                <label>
                  Maximum advertised price (₹)
                  <input
                    type="number"
                    min="0"
                    value={maxPrice}
                    onChange={(event) => setMaxPrice(event.target.value)}
                  />
                </label>

                {invalidRange && (
                  <p role="alert" className="bc-error">
                    Maximum price must be at least the minimum price.
                  </p>
                )}

                <label>
                  Minimum speed
                  <select
                    value={minSpeed}
                    onChange={(event) => setMinSpeed(event.target.value)}
                  >
                    <option value="">Any speed</option>
                    {[30, 50, 75, 100, 150, 200, 300, 400, 500, 1000].map(
                      (speed) => (
                        <option key={speed} value={speed}>
                          {speed} Mbps
                        </option>
                      ),
                    )}
                  </select>
                </label>

                <button
                  type="button"
                  className="bc-secondary"
                  onClick={resetFilters}
                >
                  Reset filters
                </button>
              </aside>

              <section aria-label="Browse plans">
                {providerFilter === "Optimum" ? <OptimumSearch /> : <>
                <div className="bc-toolbar">
                  <label>
                    Search plans
                    <input
                      type="search"
                      value={query}
                      placeholder="Plan name or provider"
                      onChange={(event) => setQuery(event.target.value)}
                    />
                  </label>

                  <label>
                    Plan type
                    <select
                      value={planType}
                      onChange={(event) =>
                        setPlanType(event.target.value as PlanType)
                      }
                    >
                      <option value="broadband">Broadband with known speed</option>
                      <option value="vouchers">Vouchers / speed not provided</option>
                      <option value="all">All plans</option>
                    </select>
                  </label>

                  <label>
                    Sort by
                    <select
                      value={sort}
                      onChange={(event) => setSort(event.target.value)}
                    >
                      <option value="provider">Provider</option>
                      <option value="speed">Speed: high to low</option>
                      <option value="price">Advertised amount: low to high</option>
                    </select>
                  </label>

                  <button
                    type="button"
                    className="bc-secondary"
                    disabled={loading}
                    onClick={() => {
                      for (const provider of providers) {
                        void loadProvider(provider.id);
                      }
                    }}
                  >
                    {loading ? "Loading…" : "Refresh"}
                  </button>
                </div>

                <div className="bc-notice">
                  Billing period not verified—confirm prices before comparing.
                  Advertised amounts may cover different durations.
                </div>

                {anchor && (
                  <section className="bc-panel bc-suggestions">
                    <h2>Similar speeds to {anchor.plan_name}</h2>
                    <p>
                      {anchor.provider} · {anchor.speed_mbps} Mbps
                    </p>
                    <p className="bc-muted">
                      Exact speeds appear first. Nearby options show their
                      difference. Location and service availability still
                      need confirmation.
                    </p>

                    {!suggestions.length && (
                      <p>
                        No other-provider plans with known speeds were found
                        in the loaded data for this location.
                      </p>
                    )}

                    {suggestions.map((plan) => {
                      const difference =
                        plan.speed_mbps! - anchor.speed_mbps!;

                      return (
                        <div className="bc-suggestion" key={plan.key}>
                          <div>
                            <strong>{plan.provider}</strong>
                            <div>{text(plan.plan_name)}</div>
                            <div>
                              {plan.speed_mbps} Mbps ·{" "}
                              {difference === 0
                                ? "Same speed"
                                : `${Math.abs(difference)} Mbps ${
                                    difference > 0 ? "faster" : "slower"
                                  }`}
                            </div>
                            <small>
                              {plan.city || "Location not specified"}
                            </small>
                          </div>

                          <button
                            type="button"
                            className="bc-secondary"
                            onClick={() => togglePlan(plan)}
                          >
                            {selection.includes(plan.key) ? "Remove" : "Compare"}
                          </button>
                        </div>
                      );
                    })}

                    <button
                      type="button"
                      className="bc-link-button"
                      onClick={() => setAnchorKey(null)}
                    >
                      Close suggestions
                    </button>
                  </section>
                )}

                <p className="bc-muted">
                  {visiblePlans.length} plans match your filters.
                </p>

                <div className="bc-grid" aria-busy={loading}>
                  {visiblePlans.map(renderCard)}

                  {loading && allPlans.length === 0 && (
                    <>
                      <span className="bc-sr-only">Loading plans…</span>
                      {[1, 2, 3, 4].map((item) => (
                        <div
                          key={item}
                          className="bc-skeleton"
                          aria-hidden="true"
                        />
                      ))}
                    </>
                  )}
                </div>

                {!loading && visiblePlans.length === 0 && (
                  <div className="bc-panel bc-empty">
                    <h2>No plans to show</h2>
                    <p>Adjust your filters or retry a provider that failed.</p>
                    <button
                      type="button"
                      className="bc-secondary"
                      onClick={resetFilters}
                    >
                      Reset filters
                    </button>
                  </div>
                )}
                </>}
              </section>
            </div>
          </>
        )}

        {view === "compare" && (
          <section className="bc-panel">
            <h2>Your comparison · {selected.length} of 3 plans</h2>

            <button
              type="button"
              className="bc-secondary"
              onClick={() => navigate("browse")}
            >
              Browse plans
            </button>

            <p className="bc-notice" style={{ marginTop: 20 }}>
              Billing period not verified—confirm prices before comparing.
            </p>

            {selected.length < 2 ? (
              <div className="bc-empty">
                <h3>Select at least two plans</h3>
                <p>
                  Choose plans from Browse plans to inspect their details
                  side by side.
                </p>
              </div>
            ) : (
              <>
                <p>
                  {!crossProvider
                    ? "Fewer than two different providers have selected plans with known speeds. You can inspect details, but there is not enough information for a cross-provider speed match."
                    : sameSpeed
                      ? "The plans with known speeds have matching advertised speeds. Billing periods, taxes, benefits and availability still need confirmation."
                      : "The selected plans have different or missing speeds. Review the speed difference before comparing their advertised prices."}
                </p>

                <label className="bc-check">
                  <input
                    type="checkbox"
                    checked={showAllFields}
                    onChange={(event) =>
                      setShowAllFields(event.target.checked)
                    }
                  />
                  Show all fields, including missing information
                </label>

                <p className="bc-muted">
                  Missing information does not mean a benefit is absent,
                  a charge is zero, or service is unavailable.
                </p>

                <div
                  className="bc-table-scroll"
                  role="region"
                  aria-label="Plan comparison table"
                  tabIndex={0}
                >
                  <table className="bc-table">
                    <thead>
                      <tr>
                        <th scope="col">Plan details</th>
                        {selected.map((plan) => (
                          <th scope="col" key={plan.key}>
                            <div>{plan.provider}</div>
                            <div>{text(plan.plan_name)}</div>
                            <button
                              type="button"
                              className="bc-secondary"
                              onClick={() => togglePlan(plan)}
                            >
                              Remove
                            </button>
                          </th>
                        ))}
                      </tr>
                    </thead>

                    <tbody>
                      {visibleComparisonRows.map((row) => (
                        <tr key={row.label}>
                          <th scope="row">{row.label}</th>
                          {selected.map((plan) => (
                            <td key={plan.key}>{row.render(plan)}</td>
                          ))}
                        </tr>
                      ))}

                      <tr>
                        <th scope="row">
                          Speed difference
                          {eligible[0] && (
                            <small className="bc-muted">
                              {" "}relative to {eligible[0].provider},{" "}
                              {eligible[0].speed_mbps} Mbps
                            </small>
                          )}
                        </th>

                        {selected.map((plan) => {
                          if (!hasSpeed(plan) || !eligible[0]) {
                            return <td key={plan.key}>Not provided</td>;
                          }

                          const difference =
                            plan.speed_mbps! - eligible[0].speed_mbps!;

                          return (
                            <td key={plan.key}>
                              {difference === 0
                                ? "Same speed"
                                : `${Math.abs(difference)} Mbps ${
                                    difference > 0 ? "faster" : "slower"
                                  }`}
                            </td>
                          );
                        })}
                      </tr>
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </section>
        )}

        {view === "about" && (
          <section className="bc-panel">
            <h2>About the saved data</h2>
            <p>
              This website displays plan records collected from public
              provider pages and returned by your API. Refresh reloads
              the saved records; it does not start a new scrape.
            </p>

            <ul>
              <li>
                Advertised prices may represent monthly rates, effective
                monthly prices, or full-duration vouchers.
              </li>
              <li>
                Billing periods, taxes, installation charges and subscription
                conditions are shown only when saved in the data.
              </li>
              <li>
                Unknown information is shown as “Not provided” or omitted
                from the default comparison view.
              </li>
              <li>
                OTT image labels are source evidence, not confirmation of
                subscription duration or eligibility.
              </li>
              <li>
                A city listing does not confirm service at your address or
                PIN code.
              </li>
              <li>
                Vouchers and records without speeds can be browsed separately
                and are excluded from speed matching.
              </li>
              <li>
                Distinct plans are retained even if their prices or speeds
                match.
              </li>
            </ul>

            <p>
              Use each plan’s source link to confirm the latest price,
              terms and service availability with the provider.
            </p>
          </section>
        )}

        <footer className="bc-muted" style={{ marginTop: 32 }}>
          BroadbandCompare · Public catalog comparison · Confirm details
          with the provider before purchasing.
        </footer>
      </main>

      {selection.length > 0 && (
        <aside className="bc-tray" aria-label="Selected comparison plans">
          <div className="bc-tray-inner">
            <strong>{selected.length} / 3 plans</strong>

            <div className="bc-tray-items">
              {selected.map((plan) => (
                <span className="bc-chip" key={plan.key}>
                  {plan.provider} ·{" "}
                  {hasSpeed(plan) ? `${plan.speed_mbps} Mbps` : text(plan.plan_name)}
                  <button
                    type="button"
                    aria-label={`Remove ${plan.provider} ${plan.plan_name}`}
                    onClick={() => togglePlan(plan)}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>

            <button
              type="button"
              className="bc-secondary"
              onClick={() => {
                setSelection([]);
                setMessage("Comparison cleared.");
              }}
            >
              Clear
            </button>

            <button
              type="button"
              className="bc-primary"
              disabled={selected.length < 2}
              onClick={() => navigate("compare")}
            >
              Compare plans
            </button>
          </div>

          {!loading && selection.length > selected.length && (
            <p className="bc-muted">
              Some saved selections are not in the loaded catalog.
              Clear the selection and choose plans again.
            </p>
          )}
        </aside>
      )}
    </div>
  );
}
