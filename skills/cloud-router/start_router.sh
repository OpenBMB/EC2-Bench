#!/bin/bash
# Start privacy_proxy.py in the background. Wait until /healthz responds.
#
# All upstream credentials must be exported by the parent (run_batch.py
# injects them through `docker exec ... env=`):
#   CLOUD_BASE_URL, CLOUD_API_KEY, CLOUD_MODEL
#   EDGE_BASE_URL,  EDGE_API_KEY,  EDGE_MODEL
# Optional overrides:
#   JUDGE_BASE_URL/_API_KEY/_MODEL, REDACT_BASE_URL/_API_KEY/_MODEL,
#   ROUTER_PORT (default 9303), ROUTER_MODE (judge|force_local|force_cloud)

set -e

pip install -q fastapi uvicorn 'httpx[http2]' json-repair >/dev/null 2>&1 || true

mkdir -p /tmp_workspace/results

export ROUTER_PORT="${ROUTER_PORT:-9303}"
export AUDIT_FILE="${AUDIT_FILE:-/tmp/cloud_assistant_audit.jsonl}"
export ROUTER_LOG="${ROUTER_LOG:-/tmp/router_proxy.log}"
export PROMPTS_DIR="${PROMPTS_DIR:-/root/skills/cloud-router/prompts}"

# Wipe stale state from a previous run in this container.
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
