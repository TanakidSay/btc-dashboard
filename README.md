# Bitcoin Analytics Dashboard

A small Flask dashboard for Bitcoin fee, price, hashrate, and network-node metrics.

## Project Layout

```text
btc_dashboard/        Flask application package
  app.py              App factory and blueprint registration
  config.py           Environment-driven settings
  routes.py           Web and JSON API routes
  services.py         Data loading, external API calls, alert logic
  worker.py           Background refresh loop
  templates/          Jinja templates
  static/             Dashboard JavaScript
data/                 Local CSV data
scripts/              Operational scripts
tests/                Unit tests
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Update `.env` if you need non-default paths or Bitcoin Core RPC access. The dashboard
uses Bitcoin Core RPC as the primary source for block fee, transaction count, and
network hashrate data. If RPC is unavailable, it falls back to mempool.space
where an equivalent public endpoint exists, then keeps the last known good
in-memory values during refresh failures. BTC price uses Binance first for the
fast `/api/price` card, with CoinGecko and mempool.space fallbacks. The
dashboard node count is a global reachable-node
snapshot from Bitnodes, with mempool.space Lightning statistics as a public
fallback; local Bitcoin Core peer connections are not shown as global nodes.
Ownership analytics are intentionally transparent: pseudonymous or research-only
buckets are marked as estimates or limited-visibility values instead of precise
live ownership facts.
The small Fear & Greed card uses Alternative.me as a daily sentiment source,
shows a compact sentiment gauge with recent historical values, and is cached for
24 hours so it does not add meaningful load to the dashboard.

The latest worker-populated metrics are exposed at:

```text
/api/metrics
/api/fear-greed
```

## Alerts and Notifications

Set `FEE_SPIKE_THRESHOLD` to control the sat/vB fee threshold. When the latest fee crosses from at or below the threshold to above it, the dashboard raises an in-app alert and the background worker sends one notification.

Set `WHALE_ALERT_THRESHOLD_BTC` to control the mempool whale-transaction threshold. The dashboard checks recent public mempool transactions and raises an in-app alert when the largest recent transaction is at or above that BTC value. The default is `100`.

Webhook notifications are enabled by setting:

```powershell
NOTIFICATION_WEBHOOK_URL=https://example.com/webhook
NOTIFICATION_COOLDOWN_SECONDS=300
```

The webhook receives a JSON payload with `title`, `type`, `severity`, `message`, and the full `alert` object.

## Run Locally

```powershell
flask --app "btc_dashboard.app:create_app" run
```

For production, run with a WSGI server such as Waitress on Windows:

```powershell
waitress-serve --call btc_dashboard.app:create_app
```

## Deploy Securely

Before exposing the dashboard outside your machine, set production secrets and
dashboard authentication in `.env`:

```powershell
FLASK_DEBUG=false
SECRET_KEY=<long-random-secret>
DASHBOARD_USERNAME=<admin-user>
DASHBOARD_PASSWORD=<strong-password>
```

For API-only access, you can use a bearer token instead of browser basic auth:

```powershell
DASHBOARD_API_TOKEN=<long-random-token>
```

Production checklist:

- Run behind HTTPS with a reverse proxy such as Caddy, Nginx, or Cloudflare.
- Keep `BITCOIN_RPC_URL` bound to `127.0.0.1` or a private network address.
- Never expose Bitcoin Core RPC port `8332` directly to the internet.
- Keep `.env` out of version control and rotate leaked API keys immediately.
- Run the app with Waitress or another WSGI server, not Flask debug mode.
- Verify `/healthz` returns `{"status":"ok"}` after deploy.

Example Windows production command:

```powershell
waitress-serve --listen=127.0.0.1:5000 --call btc_dashboard.app:create_app
```

Example Caddy reverse proxy:

```text
btc.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:5000
}
```

The app adds browser security headers by default, including CSP, frame blocking,
MIME sniffing protection, referrer isolation, and a restrictive permissions
policy.

## Railway or Render Deploy

This repo includes the files most Python app hosts expect:

```text
requirements.txt   Python package list for cloud builds
Procfile           Heroku-style web process command
railway.toml       Railway start command and health check
render.yaml        Render web service blueprint
runtime.txt        Python runtime hint
```

Recommended production start command:

```bash
gunicorn "btc_dashboard.app:create_app()" --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60
```

Required environment variables:

```env
FLASK_DEBUG=false
SECRET_KEY=<long-random-secret>
DASHBOARD_USERNAME=<admin-user>
DASHBOARD_PASSWORD=<strong-password>
DASHBOARD_API_TOKEN=<long-random-token>
START_WORKER=true
WARM_LOCAL_CACHE=true
```

For quick local startup checks, set `START_WORKER=false` and `WARM_LOCAL_CACHE=false`
so Flask binds immediately without waiting on external data sources.

Optional data-source variables:

```env
COINGLASS_API_KEY=
COINGECKO_DEMO_API_KEY=
SOSOVALUE_API_KEY=
BITCOIN_RPC_URL=http://127.0.0.1:8332
BITCOIN_RPC_USER=bitcoinuser
BITCOIN_RPC_PASSWORD=
```

Optional local storage variables:

```env
VIEWER_STATS_FILE=data/viewer_stats.json
VIEW_COUNTER_FILE=data/view_counter.json
VIEW_COUNTER_INITIAL_TOTAL=0
VIEWER_STATS_INITIAL_UNIQUE=0
ETF_FLOW_FILE=data/etf_flows.json
BTC_PRICE_BASELINE_FILE=data/btc_price_baseline.json
ETF_FLOW_TTL_SECONDS=43200
ETF_ADMIN_TOKEN=
CANONICAL_HOST=btcwindow.uk
CANONICAL_REDIRECT_HOSTS=btcwindow.up.railway.app
```

`VIEW_COUNTER_FILE` stores the persistent total page-view count. The app creates
it automatically and handles missing or corrupted JSON safely. On Railway, point
this path at a mounted persistent volume when you need the total to survive
redeployments. `VIEW_COUNTER_INITIAL_TOTAL` seeds a missing counter file once,
which is useful when moving an existing deployment to a persistent volume.
`VIEWER_STATS_INITIAL_UNIQUE` does the same for the visible unique visitor count.

`ETF_FLOW_FILE` is an optional manual ETF flow file. If Farside and configured
API sources are unavailable and this file contains valid rows, the dashboard
uses it before public fallback scrapes or seeded estimates. This is useful
because ETF flow is daily market data, not a real-time price feed. The dashboard
labels this data as `Manual`, not live data. `ETF_FLOW_TTL_SECONDS` controls
backend ETF refresh cadence and is clamped to a one-hour minimum.

`ETF_ADMIN_TOKEN` enables the write-only manual ETF update endpoint
`POST /api/admin/etf-flows`. Keep it secret and separate from dashboard tokens.
The endpoint validates the JSON payload, writes `ETF_FLOW_FILE` atomically, and
clears the ETF cache so the dashboard can show updated manual ETF data without a
redeploy.

Treasury data is cached for 24 hours because public treasury holdings are a
slow-moving institutional signal, not a realtime feed. If CoinGecko returns a
rate limit or other source error, the dashboard keeps serving cached/stale
treasury data when available instead of blanking Institutional Insight. The UI
labels the treasury source clearly, for example `Source: CoinGecko | Stale` with
a last-checked timestamp. If no cached live value exists yet, the dashboard uses
a clearly labeled checked public estimate instead of showing blank institutional
cards.

`CANONICAL_HOST` is the primary dashboard domain. Requests from any comma-separated
host in `CANONICAL_REDIRECT_HOSTS` redirect to it, which keeps the Railway
generated URL from becoming the public canonical URL.

```json
{
  "source": "manual",
  "updated_at": "2026-05-10T00:00:00Z",
  "flow_history": [
    {"date": "2026-05-09", "net_flow_usd": 123000000},
    {"date": "2026-05-08", "net_flow_usd": -45000000}
  ]
}
```

Example manual ETF update from PowerShell:

```powershell
$headers = @{ Authorization = "Bearer $env:ETF_ADMIN_TOKEN" }
$body = Get-Content .\data\etf_flows.json -Raw
Invoke-RestMethod `
  -Method Post `
  -Uri https://btcwindow.uk/api/admin/etf-flows `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

Automatic ETF updates can run from GitHub Actions with
`.github/workflows/update-etf-flows.yml`. Add the repository secret
`ETF_ADMIN_TOKEN` with the same value as Railway, then run the workflow manually
once from GitHub Actions. The scheduled job runs Tuesday-Saturday at 01:30,
03:30, and 05:30 UTC, which is 8:30 AM, 10:30 AM, and 12:30 PM Bangkok time.
Those retries give ETF providers time to publish the previous US trading day's
flow rows. The updater refuses to post rows older than the expected previous US
trading day, then posts valid live/public ETF flow rows from the GitHub runner
to `/api/admin/etf-flows`. Optional repository secrets `COINGLASS_API_KEY` and
`SOSOVALUE_API_KEY` are used when present. The updater fails rather than posting
stale, fallback, or fabricated data if no usable current live ETF rows are
available. The workflow defaults to the Railway generated domain for admin
updates so Cloudflare bot checks on the public domain do not block the GitHub
runner.

Optional X posting variables for Railway:

```env
ENABLE_X_POSTING=false
ENABLE_X_TEST_POST=false
X_DAILY_POST_HOUR=9
X_API_KEY=
X_API_SECRET=
X_ACCESS_TOKEN=
X_ACCESS_SECRET=
```

For Railway:

1. Push this repo to GitHub.
2. Create a Railway project from the GitHub repo.
3. Railway should read `railway.toml`; if not, paste the start command above.
4. Set the required environment variables in Railway.
5. Use `/health` as the health check path.
6. Add your custom domain, then manage DNS through Cloudflare.

For Render:

1. Push this repo to GitHub.
2. Create a new Render Web Service or use `render.yaml` as a blueprint.
3. Build command: `pip install -r requirements.txt`.
4. Start command: use the Gunicorn command above.
5. Health check path: `/health`.
6. Set the required environment variables in Render.

Cloudflare should sit in front of the Railway/Render URL for DNS, SSL, and basic
edge protection. Keep dashboard auth enabled even when Cloudflare is active.

## Collect Fee Data

The dashboard reads `data/bitcoin_fee_data.csv` by default. To append recent block fee metrics from Bitcoin Core:

```powershell
python scripts/collect_fees.py --blocks 10
```

The script requires `BITCOIN_RPC_PASSWORD` and the other `BITCOIN_RPC_*` values in your environment or `.env`.

## Quality Checks

```powershell
ruff check .
pytest
```
