"""Gunicorn settings for Salt Conductor, tunable through environment variables."""

import os

bind = os.environ.get("CONDUCTOR_BIND", "0.0.0.0:8080")
workers = int(os.environ.get("CONDUCTOR_WORKERS", "2"))
# gthread: the live event stream (server-sent events) keeps one thread busy per viewer
worker_class = "gthread"
threads = int(os.environ.get("CONDUCTOR_THREADS", "16"))
timeout = int(os.environ.get("CONDUCTOR_WORKER_TIMEOUT", "60"))
graceful_timeout = 15
keepalive = 5
# heartbeat files on tmpfs, so the root filesystem can be read-only
worker_tmp_dir = "/dev/shm" if os.path.isdir("/dev/shm") else None

accesslog = "-" if os.environ.get("CONDUCTOR_ACCESS_LOG", "true").lower() in ("1", "true", "yes") else None
errorlog = "-"
loglevel = os.environ.get("CONDUCTOR_LOG_LEVEL", "info").lower()
# client IP / scheme headers are only trusted when CONDUCTOR_PROXY_FIX is set (see config.py)
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
