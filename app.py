from __future__ import annotations

from btc_dashboard.app import create_app, run_dev_server

app = create_app()


if __name__ == "__main__":
    run_dev_server()
