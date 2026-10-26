# Broadband plans: saved catalogs and address-specific Optimum lookup

The existing JioFiber, ACT Fibernet and Hathway scrapers are reused. The Optimum adapter is derived from `D:\optimum-browserbase\scrape_optimum.py`; that source folder was not changed or copied wholesale.

## Run locally

Backend environment, in the existing root `.env` (never commit real values):

```dotenv
API_KEY=your-existing-backend-key
BROWSERBASE_API_KEY=your-browserbase-key
BROWSERBASE_PROJECT_ID=your-browserbase-project
REFRESH_SCHEDULE_ENABLED=false
REFRESH_INTERVAL_HOURS=24
REFRESH_TIMEOUT_SECONDS=900
```

```powershell
cd D:\airtel-plans-api
.\venv\Scripts\python.exe -m pip install -r requirements_refresh.txt
.\venv\Scripts\python.exe -m uvicorn main_clean:app --host 127.0.0.1 --port 8001
```

Use one API worker for the in-memory Optimum jobs. Avoid automatic code reload while lookups are running. A persistent backend process is required for scheduling.

Frontend server-only environment in `frontend/.env.local`:

```dotenv
BROADBAND_API_BASE_URL=http://127.0.0.1:8001
BROADBAND_API_KEY=your-existing-backend-key
```

```powershell
cd D:\airtel-plans-api\frontend
npm install
npm run dev:local
```

Open http://127.0.0.1:5173. API docs: http://127.0.0.1:8001/docs.
Browserbase credentials belong exclusively to the backend. Do not put them in Vite variables, browser code, or frontend environment files. Existing frontend server proxy credentials were reused without copying the Optimum source `.env`.

`npm run build` builds the existing hosted Vinext application, including its server-side API proxies. For hosting, configure the two `BROADBAND_` bindings with a reachable backend URL; a hosted site cannot reach this computer's loopback URL. Public hosting also requires an appropriate access policy for chargeable refresh/lookup endpoints; the local app is intended for a trusted operator.

## Official-provider refresh

The UI offers **Refresh latest plans** for each provider and **Refresh all providers**. The prior Refresh button still reloads saved data. New controls queue fresh official-site scraping, then poll persistent job status. A successful transaction reloads the affected provider without resetting filters or comparisons. A failed refresh retains the catalog and displays a stale-data warning; it does not advance “Last successfully updated.”

Authenticated endpoints (same `X-API-Key` as existing routes):

* `POST /api/refresh` with `{"provider":"act"}` (`jio`, `hathway`, or `all` also supported).
* `GET /api/refresh` returns each provider's last attempt, last success, latest job, error, scope and counts.

The optional CLI shares the queue and database lease with the API:

```powershell
.\venv\Scripts\python.exe refresh_plans.py --provider all
```

Scope is unchanged: ACT's Chennai desktop grid, Hathway's Chennai residential grid, and all six Jio prepaid catalog categories (general catalog, not verified address availability). No city can be supplied to the refresh endpoint. Scrapers do not enter signup or checkout.

### Data rules

The additive migration keeps `broadband_plans`, its existing `record_key` values, all old rows, and the legacy tables. It adds `stable_key`, `is_active`, `last_seen_at`, `updated_at`, currency, billing period and original pricing conditions. Existing timestamps are initialized from their saved `last_checked`, not from migration time. Existing captures are not relabeled as fresh refresh successes.

Stable keys are SHA-256 hashes of provider, state/city/PIN scope, and:

* A real provider plan ID plus category when supplied (ACT and Jio).
* Otherwise, normalized plan name and explicit billing-duration/options text (Hathway).

Neither price nor speed is part of the key. A change to a duration-based fallback identity is a new plan identity, rather than a guess that two offers are the same. Existing content-based frontend comparison keys are migrated when loaded; stable record IDs retain selection when prices change.

Validation checks source host, provider, location, required fields, positive finite numbers, unique identities, nonempty results, and coverage evidence. ACT requires a stable grid and Chennai selector; Hathway requires the Chennai heading, stable grid after scrolling, and no unexpanded load-more control; Jio requires every category/group and displayed group counts. This establishes completeness only for the existing extractor's documented page scope, not hidden offers or every possible nationwide promotion. A drop exceeding 25% of active records is rejected for review even with coverage evidence.

Each provider commits plan changes, price history, active/inactive changes, counts and successful job state in one transaction. Missing plans become inactive only after validated complete coverage. Empty, invalid, partial, conflicting or failed batches do not modify the catalog. Inactive records and previous prices are retained. Price-history rows retain both previous and current billing/condition metadata. Raw provider text remains in `raw_json`.

New tables: `catalog_refresh_jobs`, `provider_refresh_state`, `catalog_price_history`, `refresh_leases`, `refresh_schedule`, and `app_migrations`. APIs return active plans only. In-memory response caches observe successful refresh revisions, including CLI commits.

### Scheduling and limits

Manual refresh is enabled; scheduling defaults to off. To enable it, set `REFRESH_SCHEDULE_ENABLED=true` and `REFRESH_INTERVAL_HOURS=24` in the backend environment and restart. The first scheduled run is due one interval after initial enablement; subsequent due time is persisted in SQLite. Minimum configurable interval: one hour. These are periodic scrapes, not a continuous real-time feed.

One renewable SQLite lease controls the dispatcher and scheduler across processes using this database. A unique partial index prevents concurrent queued/running jobs for the same provider and scope. The dispatcher runs providers sequentially with a two-second polling/pacing interval. Queueing a batch that overlaps any pending provider is rejected atomically.

Each provider runs in a separate subprocess with a default 900-second deadline (configurable, bounded at 1800). Browserbase API calls use a 20-second timeout and at most one SDK retry; DOM/navigation waits are bounded. Jio detail requests pause between cards. Browser sessions have a 600-second hard TTL and normal `REQUEST_RELEASE` cleanup. A backend-only session-ID registry lets the parent request release if a worker times out or is cancelled. If release cannot be confirmed, the TTL is the final cleanup boundary. After a process crash, the queue conservatively waits out the job deadline plus browser TTL before expiring a running job.

## Optimum address lookup

Choose Optimum in the provider selector. Desktop controls are a full-address input, optional Apt input and pill-shaped **Check availability** button; controls stack on mobile. A blue location pin, accessible labels, disabled/loading states, and duplicate-submission protection are included.

Example input: `961 E 174TH ST, BRONX, NY 10460`. The backend accepts `full_address` and optional `unit`; the original structured address payload remains supported. Include street number/name, city, two-letter US state and ZIP. Missing components are rejected rather than inferred. Enter the unit in the separate Apt field. The requested placeholder `123 Main St, Anytown, USA 12345` is illustrative; without a state it is deliberately not guessed into a valid lookup.

* `POST /api/optimum/lookups`: starts a bounded background job.
* `GET /api/optimum/lookups/{id}`: status, choices or verified results.
* `POST /api/optimum/lookups/{id}/selection` with `{"choice_id":"..."}`: submits an actual returned choice.
* `DELETE /api/optimum/lookups/{id}`: cancels and releases its session.

Actual suggestions appear after submission, not as invented local autocomplete. No ambiguous match or unit is preselected. A single exact full-address suggestion can be selected automatically; unit choices always require user selection. Harmless street suffix/direction spelling and Bronx/The Bronx variations are normalized, while street number, city/state, ZIP and unit identity must match. A full address on the quote page is required before extraction or storage. Unsupported page layouts fail safely.

Successful lookups store verified address/unit, service-location ID when available, lookup ID, source URL, capture date, USD monthly prices, conditions, promotional text and supporting card text in separate `optimum_lookups` and `optimum_address_plans` tables. Address-specific USD plans are not mixed into general INR comparisons. Unknown plan fields are null. No other address's results or prior cache is substituted after a live failure. Retrieving a saved lookup after restart returns `cached: true` with its original capture date.

Selection waits expire after three minutes; overall job budget is nine minutes; Browserbase session TTL is ten minutes. Completed in-memory jobs expire after an hour; saved successes remain in SQLite. Unsuccessful jobs are not durable across restart. Maximum two active Optimum jobs and 100 in-memory jobs per process. Switching away cancels the active lookup when possible; the backend expiration remains a fallback. No account creation, checkout or order is performed.

## Tests and observed live outcome (26 October 2026)

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
cd frontend
node_modules\.bin\tsc.cmd --noEmit
npm run build
```

Browser fixture tests need the running local app and installed Chrome. They intercept Optimum/refresh operations and never intentionally start real scrapes. Unit fixtures cover upserts, price history, duplicate keys, invalid/partial/empty batches, location isolation, inactive/reactivated plans, transactions, scheduler lease, address identity, selection, timeouts and release. Browser fixtures cover extraction DOM, address choices, form validation/layout, loading, results/errors, provider rendering and comparison retention.

Live refresh attempts for Jio, ACT and Hathway failed with `Browserbase APIConnectionError` before any plans were extracted. Counts before and after: ACT **17 → 17**, Hathway **4 → 4**, Jio **30 → 30**. Audit records and the UI failure/stale-data states were verified; no last-success date was fabricated. All original 51 catalog rows were retained. Fresh official-site extraction remains unverified in this environment until backend connectivity to Browserbase is available.

The single authorized Optimum Bronx lookup returned a technical failure, no choices and no plans. Its failure UI was verified. Live address acceptance, extraction, successful SQLite insertion and verified-plan rendering have not been proven end to end; those paths passed offline fixtures only.

The migration backup is `jio_fiber_backup_20261026_080503_029156.db`. Local live reports/screenshots are under `.runtime/` and ignored by Git. No source-folder `.env` credentials, virtual environments, browser profiles or diagnostics were copied. Ignore rules exclude existing environment files, databases, virtual environments and diagnostics from future Git additions.

Manual live test scripts are deliberately excluded from unittest discovery. `tests/live_optimum_once.py` starts one real lookup and reports choices without selecting; `tests/live_refresh_once.py` starts one batch. Do not rerun them unintentionally. `tests/live_refresh_once.py --observe` only reads existing statuses and verifies the UI/database audit records.
#   f i b e r - p l a n s - c o m p a r i s o n  
 