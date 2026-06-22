#!/bin/bash
set -e
# Start nginx in background (handles upload size limits)
nginx
# Start uvicorn on internal port (nginx proxies to it)
exec uvicorn auditor.main:app --host 127.0.0.1 --port 8081
