#!/bin/bash
# Start the tap_proxy.py in the background and wait until /healthz responds.
set -e

pip install -q fastapi uvicorn 'httpx[http2]' >/dev/null 2>&1 || true
mkdir -p /tmp_workspace/results

export TAP_UPSTREAM_URL="${TAP_UPSTREAM_URL:-${OPENROUTER_BASE_URL:-https://yeysai.com/v1}}"
export TAP_UPSTREAM_KEY="${TAP_UPSTREAM_KEY:-${OPENROUTER_API_KEY:-}}"
export TAP_LOG_FILE="${TAP_LOG_FILE:-/tmp/tap_log.jsonl}"
export TAP_PORT="${TAP_PORT:-8888}"

rm -f "$TAP_LOG_FILE"

nohup python3 /root/skills/cloud-router/tap_proxy.py > /tmp/tap_proxy.log 2>&1 &
echo $! > /tmp/tap_proxy.pid

for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  if curl -sf "http://localhost:${TAP_PORT}/healthz" >/dev/null; then
    echo "[start_tap] tap_proxy ready after ${i}s (pid $(cat /tmp/tap_proxy.pid))"
    exit 0
  fi
done

echo "[start_tap] FAILED to start tap_proxy"
tail -50 /tmp/tap_proxy.log
exit 1
