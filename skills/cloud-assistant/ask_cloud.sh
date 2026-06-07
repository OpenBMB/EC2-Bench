#!/bin/bash
# Usage: bash /root/skills/cloud-assistant/ask_cloud.sh "your question here"
QUERY="$*"
RESP=$(curl -s --max-time 120 -X POST http://localhost:9200/llm/query \
  -H 'Content-Type: application/json' \
  -d "{\"query\": \"$QUERY\"}" 2>/dev/null)
echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('answer','No answer'))" 2>/dev/null || echo "$RESP"
