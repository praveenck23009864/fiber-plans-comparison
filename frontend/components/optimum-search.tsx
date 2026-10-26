"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";

type Address = { street: string; city: string; state: string; zip_code: string; unit?: string | null };
type Plan = { plan_name: string; speed_mbps: number | null; monthly_price_cents: number | null; currency: string | null; promotional_period: string | null; conditions: string | null; supporting_card_text: string };
type Job = { lookup_id: string; status: string; code?: string; message?: string; choices: { choice_id: string; label: string; service_location_id?: string | null }[]; selection_kind?: string; selection_expires_at?: string; verified_address?: Address; service_location_id?: string | null; plans: Plan[]; captured_at?: string; source_url?: string; cached?: boolean };
const terminal = new Set(["succeeded", "failed", "cancelled", "expired"]);
const addressLabel = (a: Address) => `${a.street}${a.unit ? `, Unit ${a.unit}` : ""}, ${a.city}, ${a.state} ${a.zip_code}`;

async function api(path: string, method = "GET", body?: unknown): Promise<Job> {
  const response = await fetch(`/api/optimum/lookups${path}`, {
    method, headers: { "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}), cache: "no-store", signal: AbortSignal.timeout(20000),
  });
  const payload = await response.json() as Job & { error?: string };
  if (!response.ok) throw new Error(payload.error || "Lookup request failed.");
  return payload;
}

export default function OptimumSearch() {
  const [fullAddress, setFullAddress] = useState("");
  const [unit, setUnit] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [choice, setChoice] = useState("");
  const [error, setError] = useState("");
  const generation = useRef(0);
  const active = useRef<string | null>(null);
  const locked = useRef(false);
  const mounted = useRef(true);
  const selectionLock = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      generation.current++;
      if (active.current) void api(`/${active.current}`, "DELETE").catch(() => {});
    };
  }, []);

  useEffect(() => {
    if (!job || terminal.has(job.status)) return;
    const token = generation.current;
    let disposed = false;
    const timer = setTimeout(async () => {
      try {
        const next = await api(`/${job.lookup_id}`);
        if (disposed || !mounted.current || token !== generation.current) return;
        setJob(next);
        if (terminal.has(next.status)) { setBusy(false); locked.current = false; active.current = null; }
      } catch (e) {
        if (disposed || !mounted.current || token !== generation.current) return;
        setError(`${e instanceof Error ? e.message : "Status polling failed."} Search stopped; no fresh results are available.`);
        void api(`/${job.lookup_id}`, "DELETE").catch(() => {});
        setJob(null); setBusy(false); locked.current = false; active.current = null;
      }
    }, 1500);
    return () => { disposed = true; clearTimeout(timer); };
  }, [job]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked.current) return;
    const clean = { full_address: fullAddress.trim(), unit: unit.trim() || null };
    if (!/^\d+[\w-]*\s+.+,\s*[^,]+,\s*[a-z]{2}[ ,]+\d{5}(?:-\d{4})?(?:,?\s*(?:USA|United States))?$/i.test(clean.full_address) || /\b(?:apt|apartment|unit|suite)\b|#/i.test(clean.full_address)) {
      setError("Include street number and name, city, two-letter state, and ZIP (for example: 961 E 174TH ST, BRONX, NY 10460). Put the unit in the Apt field."); return;
    }
    locked.current = true; setBusy(true); setError(""); setJob(null); setChoice("");
    const token = ++generation.current;
    try {
      const next = await api("", "POST", clean);
      if (!mounted.current || token !== generation.current) { void api(`/${next.lookup_id}`, "DELETE").catch(() => {}); return; }
      active.current = next.lookup_id; setJob(next);
      if (terminal.has(next.status)) { setBusy(false); locked.current = false; active.current = null; }
    } catch (e) {
      if (mounted.current && token === generation.current) {
        setError(e instanceof Error ? e.message : "Lookup failed."); setBusy(false); locked.current = false;
      }
    }
  }

  async function cancel() {
    const token = ++generation.current;
    const id = active.current; active.current = null;
    setJob(null); setBusy(false); locked.current = false; setSelecting(false); selectionLock.current = false; setError("Lookup cancelled.");
    if (id) await api(`/${id}`, "DELETE").catch(() => {
      if (mounted.current && token === generation.current) setError("Cancellation could not be confirmed. The backend will expire the session automatically.");
    });
  }

  async function select() {
    if (!job || !choice || selectionLock.current) return;
    selectionLock.current = true; setSelecting(true); setError("");
    const token = generation.current;
    try {
      const next = await api(`/${job.lookup_id}/selection`, "POST", { choice_id: choice });
      if (mounted.current && token === generation.current) {
        setJob(next); setChoice("");
        if (terminal.has(next.status)) { setBusy(false); locked.current = false; active.current = null; }
      }
    } catch (e) {
      if (mounted.current && token === generation.current) setError(e instanceof Error ? e.message : "Selection failed.");
    } finally {
      if (token === generation.current) { setSelecting(false); selectionLock.current = false; }
    }
  }

  return <div className="optimum-search">
    <header><h2>Optimum · search a US address</h2><p>Plans depend on the verified service address. Coverage is not guaranteed. Prices are in USD.</p></header>
    <form onSubmit={submit} aria-label="Optimum address search">
      <div className="optimum-address-row">
        <label className="optimum-address-field"><span className="bc-sr-only">Full service address</span>
          <svg className="optimum-pin" aria-hidden="true" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg>
          <input required disabled={busy} minLength={8} maxLength={300} autoComplete="street-address" aria-describedby="optimum-address-help" placeholder="123 Main St, Anytown, USA 12345" value={fullAddress} onChange={e => setFullAddress(e.target.value)} />
        </label>
        <label className="optimum-unit-field"><span className="bc-sr-only">Apartment or unit (optional)</span><input disabled={busy} maxLength={40} autoComplete="address-line2" placeholder="Apt" value={unit} onChange={e => setUnit(e.target.value)} /></label>
        <button className="optimum-check" disabled={busy || !fullAddress.trim()} type="submit">{busy ? "Checking…" : "Check availability"}</button>
      </div>
      <p id="optimum-address-help" className="bc-muted">Include street, city, two-letter state and ZIP. Optimum’s address choices appear after checking.</p>
      {busy && <button className="bc-secondary" type="button" onClick={() => void cancel()}>Cancel search</button>}
    </form>
    {busy && <p role="status">{job?.status === "awaiting_selection" ? "Your selection is required to continue." : "Checking the address and retrieving plans. This can take several minutes."}</p>}
    {error && <p role="alert" className="bc-error">{error}</p>}
    {job?.status === "awaiting_selection" && <section className="bc-panel" aria-label="Address choices">
      <h3>{job.selection_kind === "unit" ? "Choose your unit" : "Confirm your service address"}</h3>
      <p>Choose the record that belongs to you. No choice is preselected. If none match, cancel and correct the address.</p>
      {job.selection_expires_at && <p>Selection expires at {new Date(job.selection_expires_at).toLocaleTimeString()}.</p>}
      <fieldset disabled={selecting}><legend>Optimum returned these choices</legend>{job.choices.map((c, i) => <label className="optimum-choice" key={c.choice_id}><input type="radio" name="optimum-choice" value={c.choice_id} checked={choice === c.choice_id} onChange={() => setChoice(c.choice_id)} />{c.label}{c.service_location_id ? ` · Location ${c.service_location_id}` : ` · Choice ${i + 1}`}</label>)}</fieldset>
      <button className="bc-primary" disabled={!choice || selecting} onClick={() => void select()}>{selecting ? "Continuing…" : "Continue with selected address"}</button>
    </section>}
    {job && ["failed", "expired", "cancelled"].includes(job.status) && <div role="alert" className="bc-error"><strong>{job.code?.replaceAll("_", " ")}: </strong>{job.message}</div>}
    {job?.status === "succeeded" && job.verified_address && <section aria-label="Optimum results">
      <h3>Verified service address</h3><p>{addressLabel(job.verified_address)}</p>
      {job.service_location_id && <p>Service-location ID: {job.service_location_id}</p>}
      <p>{job.cached ? "Saved results" : "Captured"}: {job.captured_at ? new Date(job.captured_at).toLocaleString() : "Not provided"}</p>
      <div className="bc-grid">{job.plans.map((plan, i) => <article className="bc-panel optimum-plan" key={`${plan.plan_name}-${i}`}>
        <h3>{plan.plan_name}</h3><p>{plan.speed_mbps === null ? "Speed not provided" : `${plan.speed_mbps} Mbps`}</p>
        <strong>{plan.monthly_price_cents === null ? "Price not provided" : `${(plan.monthly_price_cents / 100).toFixed(2)} ${plan.currency || "Currency not provided"} / month`}</strong>
        <p>Promotional / price-protection period: {plan.promotional_period || "Not provided"}</p>
        <p className="optimum-conditions">Pricing conditions: {plan.conditions || "Not provided"}</p>
        <details><summary>Supporting card text</summary><pre>{plan.supporting_card_text}</pre></details>
        <p>Capture date: {job.captured_at ? new Date(job.captured_at).toLocaleString() : "Not provided"}</p>
        {job.source_url?.startsWith("https://www.optimum.com/") && <a href={job.source_url} target="_blank" rel="noreferrer">Optimum source (opens new tab)</a>}
      </article>)}</div>
    </section>}
  </div>;
}
