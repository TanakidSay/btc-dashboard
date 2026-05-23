# Agent Guide for btc-dashboard

This file is quick working context for agents and contributors entering the
`btc-dashboard` project. Read it first, then open the specific files relevant to
the task.

## Project Overview

`btc-dashboard` is a Flask dashboard for Bitcoin analytics, including fee,
price, hashrate, network-node metrics, ownership analytics, alerts, viewer
tracking, and optional X posting.

Current public production URL:

- Primary domain: `https://btcwindow.uk`
- Railway generated domain: `https://btcwindow.up.railway.app`
- The app redirects the Railway generated domain to the primary domain, except
  health checks.

Important paths:

- `btc_dashboard/app.py` creates the Flask app, auth, security headers, and worker startup.
- `btc_dashboard/routes.py` contains the dashboard and JSON API routes.
- `btc_dashboard/services.py` handles data loading, external APIs, caching, fallbacks, and alerts.
- `btc_dashboard/signal_engine.py` builds analytic signals and post text.
- `btc_dashboard/worker.py` runs the background refresh loop.
- `btc_dashboard/x_poster.py` handles X posting integration and policy.
- `btc_dashboard/config.py` maps environment variables into settings.
- `btc_dashboard/templates/dashboard.html` is the main UI template.
- `btc_dashboard/static/dashboard.js` handles client-side refresh and interactions.
- `tests/` contains the Pytest suite.
- `data/` contains local CSV/state files used by the app.

Current notable data files:

- `data/etf_flows.json` is the bundled manual ETF flow file.
- `data/view_counter.json`, `data/viewer_stats.json`, and
  `data/viewer_analytics.json` are local runtime state when not using Railway
  Volume paths.
- `data/btc_price_baseline.json` stores the 7 AM Bangkok BTC price baseline
  used for the dashboard's 24h-style change comparison.

## Common Commands

Local setup:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Run locally:

```powershell
flask --app "btc_dashboard.app:create_app" run
```

Run with a production WSGI server on Windows:

```powershell
waitress-serve --call btc_dashboard.app:create_app
```

Quality checks:

```powershell
ruff check .
pytest
```

On this machine, these commands are commonly used:

```powershell
py -3.12 -B -m ruff check .
py -3.12 -B -m pytest
```

## Coding Guidelines

- Use Python 3.11+ and follow the style in `pyproject.toml`.
- Ruff is configured with `E`, `F`, `I`, `UP`, and `B`; line length is 100.
- When changing behavior in `services.py`, `routes.py`, `worker.py`,
  `signal_engine.py`, or `x_poster.py`, update or add focused tests.
- Be careful with the background worker. Tests should disable or control worker
  startup when a thread is not part of the behavior under test.
- Never hardcode secrets, API keys, webhook URLs, or credentials.
- Do not commit `.env`, caches, logs, or unnecessary generated state.
- If UI behavior changes, inspect both `dashboard.html` and `dashboard.js`.
- If an API response shape changes, update the tests and the frontend consumer
  together.

## Data Sources and Fallbacks

The app is designed to tolerate external data-source failures:

- Bitcoin Core RPC is the primary source for block fees, transaction counts, and hashrate.
- If RPC is unavailable, public fallbacks such as mempool.space are used where equivalent endpoints exist.
- BTC price uses Binance first, then falls back to CoinGecko and mempool.space.
- BTC price change is compared against a persisted Bangkok 7 AM baseline so it
  does not collapse to zero between refreshes.
- Node count uses Bitnodes as a global reachable-node snapshot.
- Fear & Greed is a compact sentiment gauge card sourced from Alternative.me. It
  uses daily data, shows recent historical values, is cached for 24 hours, and
  should stay lightweight rather than becoming another heavy signal section.
- ETF flow uses Farside first when available. If API keys are configured, it can
  use CoinGlass and SoSoValue. If those sources are unavailable, it uses the
  manual ETF JSON file before public fallback scrapes and seeded estimates.
- If `ETF_FLOW_FILE=/data/etf_flows.json` is configured on Railway and the file
  does not exist yet, the app seeds it once from bundled `data/etf_flows.json`.
- Seeded ETF fallback data must remain clearly labeled as fallback estimate; do
  not present it as live data.
- Ownership analytics must remain transparent. Estimates and limited-visibility values
  should never be presented as exact live ownership facts.
- Treasury fallback top holders are aligned to the checked CoinGecko-style public
  company list and should not invent unclear/non-canonical holders.
- During refresh failures, preserve last known good values where the existing design supports it.

When adding a fallback, make the source, freshness, and failure mode clear in
code or tests.

ETF flow is daily US market data, not a realtime price feed. Verify latest ETF
facts before updating manual values.

## Important Environment Variables

See `.env.example` and `btc_dashboard/config.py` for the complete list.

Commonly relevant variables:

- `SECRET_KEY`
- `DASHBOARD_USERNAME`
- `DASHBOARD_PASSWORD`
- `DASHBOARD_API_TOKEN`
- `START_WORKER`
- `BITCOIN_RPC_URL`
- `BITCOIN_RPC_USER`
- `BITCOIN_RPC_PASSWORD`
- `FEE_SPIKE_THRESHOLD`
- `WHALE_ALERT_THRESHOLD_BTC`
- `NOTIFICATION_WEBHOOK_URL`
- `ENABLE_X_POSTING`
- `ENABLE_X_TEST_POST`
- `X_API_KEY`
- `X_API_SECRET`
- `X_ACCESS_TOKEN`
- `X_ACCESS_SECRET`
- `COINGLASS_API_KEY`
- `COINGECKO_DEMO_API_KEY`
- `SOSOVALUE_API_KEY`
- `VIEW_COUNTER_FILE`
- `ALERTS_HISTORY_FILE`
- `VIEWER_STATS_FILE`
- `VIEWER_ANALYTICS_FILE`
- `ETF_FLOW_FILE`
- `ETF_ADMIN_TOKEN`
- `BTC_PRICE_BASELINE_FILE`
- `CANONICAL_HOST`
- `CANONICAL_REDIRECT_HOSTS`

Production must use real secrets and dashboard authentication.

Railway production currently uses a mounted Volume at `/data`. Persistent state
should point at files inside that mount, for example:

```env
VIEW_COUNTER_FILE=/data/view_counter.json
VIEWER_STATS_FILE=/data/viewer_stats.json
VIEWER_ANALYTICS_FILE=/data/viewer_analytics.json
ALERTS_HISTORY_FILE=/data/alerts_history.json
ETF_FLOW_FILE=/data/etf_flows.json
ETF_ADMIN_TOKEN=<long-random-secret>
BTC_PRICE_BASELINE_FILE=/data/btc_price_baseline.json
CANONICAL_HOST=btcwindow.uk
CANONICAL_REDIRECT_HOSTS=btcwindow.up.railway.app
```

## Security Notes

- `btc_dashboard/app.py` provides Basic Auth, Bearer token auth, and security headers.
- `/healthz` is intentionally accessible without auth for health checks.
- `/health` is also available for host health checks.
- Never expose Bitcoin Core RPC port `8332` directly to the internet.
- Production should run behind HTTPS through a reverse proxy such as Caddy,
  Nginx, or Cloudflare.
- Production is currently fronted by Cloudflare. Cloudflare should cache static
  assets under `/static/*` only; do not cache `/api/*` because price, ETF, and
  viewer metrics must remain fresh.
- When changing `btc_dashboard/static/dashboard.js`, bump the `v=` cache-buster
  in `btc_dashboard/templates/dashboard.html`; otherwise Cloudflare/browser
  static caching can serve old JavaScript while new HTML is already deployed.
- Do not weaken CSP or other security headers unless the reason is clear and
  covered by tests.

Viewer tracking notes:

- `Total Views` increments on page requests to `/`.
- `Unique Visitors` is based on a persistent fingerprint, roughly IP plus
  User-Agent, so the same visitor normally does not increase unique count again
  on later days unless their fingerprint changes.
- `/api/viewers` returns the summary card values.
- `/api/viewer-analytics` returns aggregate sources, referrers, devices,
  browsers, countries, paths, recent events, and suppressed duplicate events.
- Viewer analytics intentionally stores aggregate/privacy-preserving data rather
  than raw IP addresses.

Alert history notes:

- `/api/alert` returns both currently active alerts and `recent_alerts`.
- Recent alerts are stored in `ALERTS_HISTORY_FILE` and should point to the
  Railway Volume in production, for example `/data/alerts_history.json`.
- Alert history keeps a small deduped record so transient alerts do not flash
  and disappear immediately from the UI.
- Do not store IP addresses or user-identifying data in alert history.

Time display notes:

- Backend/API timestamps should remain UTC internally.
- Frontend display should stay user-friendly and use the browser/user local
  timezone for visible timestamps and chart labels.
- Do not add `Timezone: UTC` labels to chart sections while frontend display is
  local time; it is confusing for users.
- Static metric cards should not have repeated timezone labels.

Manual ETF update notes:

- `POST /api/admin/etf-flows` updates `ETF_FLOW_FILE` without redeploying.
- It requires `Authorization: Bearer <ETF_ADMIN_TOKEN>`.
- Keep `ETF_ADMIN_TOKEN` separate from dashboard auth tokens.
- In Railway, set the token value without angle brackets. Use
  `ETF_ADMIN_TOKEN=actual-secret`, not `ETF_ADMIN_TOKEN=<actual-secret>`.
- After changing `ETF_ADMIN_TOKEN` in Railway, redeploy/restart the service so
  the Flask process reloads the environment.
- The endpoint is POST-only, validates the manual JSON payload, writes the file
  atomically, and clears ETF cache after a successful update.
- The endpoint merges incoming `flow_history` rows with existing manual history
  by date, so sending only the latest date appends/replaces that date instead
  of wiping older chart history.
- A previous production incident left ETF chart history with only one bar after
  a one-row admin update. The fix is deployed: admin updates now merge by date.
  If this happens again, restore by posting the full manual history once, then
  verify `((Invoke-RestMethod "https://btcwindow.uk/api/etf").flow_history).Count`.
- After the 2026-05-15 restore, the expected history count was `14` rows. Future
  counts should be at least that unless old rows are intentionally pruned.
- Do not assume latest-date verification is enough; always verify both
  `latest_date` and `flow_history` count after ETF admin updates.
- Do not expose this endpoint in the frontend.
- On Windows PowerShell, avoid pasted JSON here-strings if `Invoke-RestMethod`
  reports invalid control characters. Build the payload as a PowerShell object
  and run `ConvertTo-Json -Depth 5 -Compress`.
- Clean the local token before building headers:

```powershell
$env:ETF_ADMIN_TOKEN = "actual-secret"
$token = ($env:ETF_ADMIN_TOKEN -replace '[\x00-\x1F\x7F]', '').Trim()
$headers = @{ Authorization = "Bearer $token" }
```

- Minimal successful ETF update pattern:

```powershell
$payload = @{
  source = "manual"
  updated_at = "2026-05-17T00:00:00Z"
  flow_history = @(
    @{ date = "2026-05-15"; net_flow_usd = -290400000 }
  )
}
$body = $payload | ConvertTo-Json -Depth 5 -Compress
Invoke-RestMethod -Method Post -Uri "https://btcwindow.uk/api/admin/etf-flows" `
  -Headers $headers -ContentType "application/json" -Body $body
```

- After updating, verify production with:

```powershell
Invoke-RestMethod "https://btcwindow.uk/api/etf" |
  Select-Object latest_date, latest_net_flow_usd, source_label
((Invoke-RestMethod "https://btcwindow.uk/api/etf").flow_history).Count
```

Automatic ETF update notes:

- `.github/workflows/update-etf-flows.yml` runs the ETF updater Tuesday-Saturday
  at `01:30`, `03:30`, and `05:30 UTC`, which is `8:30 AM`, `10:30 AM`, and
  `12:30 PM Bangkok time`.
- IBIT/BlackRock ETF flow rows are normally available around `7:00 AM Bangkok
  time`, but GitHub scheduled workflows can run late and public ETF sources can
  lag, so the workflow uses several morning retries.
- The updater refuses to post source rows older than the expected previous US
  trading day. This prevents stale rows such as a `May 20` public fallback row
  from being posted as the current value on `May 22`.
- If production is behind by more than one trading day, the updater may catch up
  by accepting the next trading day after production's current latest row before
  requiring the newest expected date. For example, if production is on `May 20`
  and expected is `May 22`, a valid `May 21` source row can still be posted.
- The updater must filter non-date table rows such as `Total` before posting to
  `/api/admin/etf-flows`; the admin endpoint rejects invalid manual dates.
- The workflow runs `python scripts/update_etf_flows.py`.
- Required GitHub repository secret: `ETF_ADMIN_TOKEN`. Use the same value as
  Railway `ETF_ADMIN_TOKEN`.
- Optional GitHub repository variable: `BTCWINDOW_BASE_URL`; default is
  `https://btcwindow.up.railway.app` so GitHub Actions can bypass Cloudflare
  bot checks on the public domain for the admin update.
- Optional GitHub repository secrets: `COINGLASS_API_KEY`,
  `SOSOVALUE_API_KEY`.
- The updater fetches live/public ETF rows from the GitHub runner, converts them
  to the manual admin payload, and posts to `/api/admin/etf-flows`.
- The updater intentionally fails instead of posting stale rows, fallback
  estimates, manual data, or fabricated live data if no usable current live rows
  are available.
- The script checks current production `/api/etf` first and skips the admin POST
  when the latest date is already current unless `--force` is used. If that
  read-only check is blocked, the script continues to the admin POST instead of
  failing early.
- Manual workflow run: GitHub repository -> Actions -> `Update ETF flows` ->
  `Run workflow`.
- Local dry run:

```powershell
py -3.12 -B scripts/update_etf_flows.py --dry-run
```

Treasury data notes:

- Treasury BTC holdings are slow-moving institutional data, not a realtime feed.
- Backend treasury cache TTL is 24 hours to reduce CoinGecko rate-limit pressure.
- The treasury loader tries CoinGecko public/company endpoints once each, then
  uses cached/stale data if CoinGecko returns 429 or another source error.
- The frontend must show a clear treasury source label such as
  `Source: CoinGecko | Live` or `Source: CoinGecko | Stale` plus `Last checked`.
- Institutional Insight may use cached/stale treasury data because it is a
  broad institutional context signal, not an intraday trading signal.
- A manual treasury JSON fallback similar to `ETF_FLOW_FILE` is a valid future
  hardening step, but it is not required for the current MVP.

## Testing Notes

Choose tests based on the area changed:

- Routes, auth, and security: `tests/test_app_security.py`
- Services, data, fallbacks, and alerts: `tests/test_services.py`
- Frontend refresh contract: `tests/test_frontend_price_refresh.py`
- Worker behavior: `tests/test_worker.py`
- Signal logic: `tests/test_signal_engine.py`
- X posting policy: `tests/test_x_poster.py`

Before handing off, run at least:

```powershell
py -3.12 -B -m ruff check .
py -3.12 -B -m pytest
```

If checks cannot be run, record the reason clearly.

## Deployment Notes

The repo includes deployment files for multiple hosts:

- `Procfile`
- `railway.toml`
- `render.yaml`
- `runtime.txt`
- `deploy/Caddyfile`

Health check paths may be `/health` or `/healthz` depending on the host/config.
Check the actual route definitions in `btc_dashboard/routes.py` before changing
deployment settings.

Railway production details:

- `railway.toml` starts Gunicorn bound to `0.0.0.0:$PORT`.
- Railway custom domain should target port `8080` / the detected web port shown
  by Railway for this service.
- The public custom domain is `btcwindow.uk`.
- Cloudflare SSL mode is expected to be `Full` or `Full (strict)`.
- Cloudflare DNS includes Railway custom-domain verification records and routes
  `btcwindow.uk` to Railway.

## Working Agreement

- Start with `git status --short` and preserve any existing user changes.
- Use `rg` and `rg --files` for searches.
- Keep changes scoped to the request.
- Avoid unrelated refactors.
- Verify current external facts before relying on data that may have changed,
  including prices, ETF data, API behavior, laws, or hosting-platform behavior.
- Do not commit or push automatically. Ask the user before every commit and
  every push unless the current user message explicitly says to commit or push.
- Final responses should briefly state what changed and which checks were run.
