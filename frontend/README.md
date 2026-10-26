# Broadband Compare

React dashboard with a Vinext server-side proxy. The browser requests only
`/api/plans/jio`, `/api/plans/act`, and `/api/plans/hathway` on the same origin.
The proxy maps these to the existing FastAPI endpoints, adds X-API-Key on the
server, applies a 12-second timeout, blocks redirects, strips unexpected fields,
and never returns upstream error bodies or credentials.

## Connect locally on Windows

Keep the FastAPI project and this website in separate folders. Use Node >=22.13.
1. Start the existing backend in its terminal:
   `cd D:\airtel-plans-api`
   `.\venv\Scripts\python.exe -m uvicorn main_clean:app --port 8001`
2. Open this website source folder in a second terminal and run `npm install`.
3. Set server environment variables in that second terminal. Enter your existing
   API_KEY value locally; do not commit or share it:
   ```powershell
   $env:BROADBAND_API_BASE_URL = 'http://127.0.0.1:8001'
   $secret = Read-Host 'Existing FastAPI API_KEY' -AsSecureString
   $env:BROADBAND_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
   npm run dev
   ```
4. Open the local URL printed by the website dev server. Keep both terminals open.
   The React browser code never receives BROADBAND_API_KEY. CORS changes to
   FastAPI are unnecessary because the proxy makes the backend request.

## Hosted connection

A hosted website cannot access your PC's 127.0.0.1. Deploy FastAPI to a reachable
HTTPS service, or use a supported private service connection. Configure
BROADBAND_API_BASE_URL and secret BROADBAND_API_KEY in the Site's server runtime
settings and redeploy. Do not paste API keys into chat, URLs or frontend code.
The hosted website remains private by default. If made public, the read proxy
makes plan data accessible to its visitors even though the upstream key is hidden.
Without configuration, each provider shows a truthful connection state. No fake
catalog, fake record counts, offers or availability is injected.

## Features / data contract

- Independent provider requests/retries; a failed provider does not hide others.
- Provider, city, numeric price, minimum speed, and text filters.
- Broadband, vouchers/other, and all-plan modes. Unknown locations remain visible
  under All locations / Location not specified, never silently mapped to Chennai.
- Up to 3 comparisons persisted as plan identities (not credentials) in localStorage.
  Current response data is used after reload; unavailable selections are removable.
- Exact cross-provider speed matches first; nearest-speed options follow with
  numeric differences. No price/value ranking without equivalent billing terms.
- Same speed/price records are preserved. Distinct API records receive occurrence
  suffixes so identical 12-field projections do not get silently deduplicated.
  For stable identities across changing identical records, extend the backend with
  a persistent plan ID; current identity is derived from its available fields.
- The API currently does not expose billing period, taxes, or price basis, so
  comparisons explicitly remain provisional. Expiry is displayed as supplied.
- Source links allow only HTTP(S), open in a new tab, and are never auto-fetched.
- Accessible labels, keyboard focus, reduced motion, skeleton/empty/error states,
  responsive expandable filters and scrollable comparison tables.

Components are in app/page.tsx; pure plan utilities in lib/plans.ts; server proxy
in app/api/plans/[provider]/route.ts; styles in app/globals.css.

## Verification

Build: `npm run build`. Logic fixtures test matching, null speed handling,
identity preservation, source URL safety and INR formatting. Live integration
requires your running backend and server-only environment configuration.
