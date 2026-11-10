#!/bin/bash
set -e

pip install -q fastapi uvicorn 'httpx[http2]' json-repair >/dev/null 2>&1 || true

mkdir -p /tmp_workspace/results

export ROUTER_PORT="${ROUTER_PORT:-9303}"
export AUDIT_FILE="${AUDIT_FILE:-/tmp/cloud_assistant_audit.jsonl}"
export ROUTER_LOG="${ROUTER_LOG:-/tmp/router_proxy.log}"

rm -f "$AUDIT_FILE" "$ROUTER_LOG"

nohup python3 /root/skills/cloud-router/privacy_proxy.py > /tmp/router_proxy.stdout 2>&1 &
echo $! > /tmp/router_proxy.pid

for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  sleep 1
  if curl -sf "http://localhost:${ROUTER_PORT}/healthz" >/dev/null; then
    echo "[start_router] privacy_proxy ready after ${i}s (pid $(cat /tmp/router_proxy.pid))"
    curl -s "http://localhost:${ROUTER_PORT}/healthz"
    echo
    exit 0
  fi
done

echo "[start_router] FAILED to start privacy_proxy"
echo "--- stdout ---"
tail -100 /tmp/router_proxy.stdout
echo "--- log ---"
tail -100 "$ROUTER_LOG" || true
exit 1
