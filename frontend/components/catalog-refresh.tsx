"use client";
import { useCallback, useEffect, useRef, useState } from "react";

type RefreshJob = { job_id: string; status: "queued" | "running" | "completed" | "failed"; error: string | null; added: number; updated: number; unchanged: number; inactive: number };
type ProviderRefresh = { provider: string; name: string; scope: string; last_success_at: string | null; last_attempt_at: string | null; last_error: string | null; job: RefreshJob | null };
type Status = { providers: ProviderRefresh[]; schedule_enabled: boolean; interval_hours: number };
const active = (p: ProviderRefresh) => p.job?.status === "queued" || p.job?.status === "running";

export default function CatalogRefresh({ onUpdated }: { onUpdated: (provider: string) => Promise<void> }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const lock = useRef(false);
  const seen = useRef(new Set<string>());
  const mounted = useRef(true);
  const sequence = useRef(0);
  const readStatus = useCallback(async () => {
    const token = ++sequence.current;
    const response = await fetch("/api/refresh", { cache: "no-store", signal: AbortSignal.timeout(12000) });
    const payload = await response.json() as Status & { error?: string };
    if (!response.ok) throw new Error(payload.error || "Refresh status unavailable.");
    if (!mounted.current || token !== sequence.current) return;
    setStatus(payload);
    for (const provider of payload.providers) {
      if (provider.job?.status === "completed" && !seen.current.has(provider.job.job_id)) {
        seen.current.add(provider.job.job_id);
        void onUpdated(provider.provider);
      }
    }
  }, [onUpdated]);

  useEffect(() => {
    mounted.current = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try { await readStatus(); }
      catch (e) { if (mounted.current) setError(e instanceof Error ? e.message : "Refresh status unavailable."); }
      if (mounted.current) timer = setTimeout(poll, 2500);
    }
    void poll();
    return () => { mounted.current = false; sequence.current++; clearTimeout(timer); };
  }, [readStatus]);

  async function refresh(provider: string) {
    if (lock.current) return;
    lock.current = true; setSubmitting(true); setError("");
    try {
      const response = await fetch("/api/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider }), signal: AbortSignal.timeout(15000) });
      if (!response.ok) {
        const payload = await response.json() as { error?: string };
        throw new Error(payload.error || "Could not queue refresh.");
      }
      await readStatus();
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : "Refresh failed.");
    } finally {
      lock.current = false;
      if (mounted.current) setSubmitting(false);
    }
  }

  return <section className="bc-panel catalog-refresh" aria-label="Refresh official catalogs">
    <div className="catalog-refresh-heading"><div><h2>Latest provider catalogs</h2><p>Refresh from official pages. ACT and Hathway: Chennai. JioFiber: general catalog.</p></div>
      <button className="bc-primary" disabled={!status || submitting || status.providers.some(active)} onClick={() => void refresh("all")}>Refresh all providers</button>
    </div>
    {error && <p className="bc-error" role="alert">{error}</p>}
    {!status && <p role="status">Loading refresh status…</p>}
    <div className="catalog-refresh-grid">{status?.providers.map(p => <section key={p.provider} aria-label={`${p.name} refresh`}>
      <h3>{p.name}</h3>
      <p>Last successfully updated: {p.last_success_at ? new Date(p.last_success_at).toLocaleString() : "No verified refresh recorded"}</p>
      {p.last_attempt_at && <p>Last attempted: {new Date(p.last_attempt_at).toLocaleString()}</p>}
      <p role="status">{p.job ? p.job.status.charAt(0).toUpperCase() + p.job.status.slice(1) : "Ready for manual refresh"}</p>
      {p.job?.status === "completed" && <p>{p.job.added} added · {p.job.updated} updated · {p.job.unchanged} unchanged · {p.job.inactive} inactive</p>}
      {(p.last_error || p.job?.status === "failed") && <p className="bc-error" role="alert">Saved data may be stale. {p.last_error || p.job?.error} Previous plans remain available.</p>}
      <button className="bc-secondary" disabled={submitting || active(p)} onClick={() => void refresh(p.provider)}>Refresh latest plans<span className="bc-sr-only"> for {p.name}</span></button>
    </section>)}</div>
    {status && <p className="bc-muted">{status.schedule_enabled ? `Scheduled every ${status.interval_hours} hours.` : "Manual refresh enabled. Scheduled refresh is off."} Updates are periodic scrapes, not a continuous real-time feed.</p>}
  </section>;
}
