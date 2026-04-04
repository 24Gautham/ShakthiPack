"""
Gunicorn production configuration.
Usage: gunicorn -c gunicorn.conf.py app:app
"""
import multiprocessing
import os

# Network
bind    = f"0.0.0.0:{os.environ.get('PORT', 5000)}"
backlog = 2048

# Workers
workers          = int(os.environ.get("WEB_CONCURRENCY", multiprocessing.cpu_count() * 2 + 1))
worker_class     = "sync"
worker_connections = 1000
timeout          = 60
keepalive        = 5
max_requests     = 1000
max_requests_jitter = 50

# Security
limit_request_line   = 4096
limit_request_fields = 100
limit_request_field_size = 8190

# Logging
accesslog  = "-"
errorlog   = "-"
loglevel   = os.environ.get("LOG_LEVEL", "info").lower()
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sµs'

# Process naming
proc_name = "shakthipack"

# Graceful restarts
graceful_timeout = 30
