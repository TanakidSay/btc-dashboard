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

The latest worker-populated metrics are exposed at:

```text
/api/metrics
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
```

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
```

`VIEW_COUNTER_FILE` stores the persistent total page-view count. The app creates
it automatically and handles missing or corrupted JSON safely. On Railway, point
this path at a mounted persistent volume when you need the total to survive
redeployments. `VIEW_COUNTER_INITIAL_TOTAL` seeds a missing counter file once,
which is useful when moving an existing deployment to a persistent volume.
`VIEWER_STATS_INITIAL_UNIQUE` does the same for the visible unique visitor count.

`ETF_FLOW_FILE` is an optional manual ETF flow file. If it contains valid rows,
the dashboard uses it before live ETF scraping so production hosts do not depend
on Farside availability. This is useful because ETF flow is daily market data,
not a real-time price feed. The dashboard labels this data as `Manual`, not live
data. `ETF_FLOW_TTL_SECONDS` controls backend ETF refresh cadence and is clamped
to a one-hour minimum.

If live treasury sources are unavailable, the dashboard uses a clearly labeled
checked public estimate instead of showing blank institutional cards.

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
