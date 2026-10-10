"""One worker keeps SQLite access and login throttling in a single process."""

import os

bind = f"{os.environ.get('HOST', '0.0.0.0')}:{os.environ.get('PORT', '3000')}"
workers = 1
threads = 4
timeout = 30
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
# Do not trust forwarded headers from arbitrary network peers.
forwarded_allow_ips = os.environ.get("TRUSTED_PROXY_IPS", "127.0.0.1,::1")
