#!/usr/bin/env bash
# Stops a QA server and proves it is down by port probe, not pgrep.
# Usage: bash scripts/stop_qa.sh [PORT]
set -u
PORT="${1:-8737}"
pkill -9 -f "uvicorn app.main.*--port $PORT" 2>/dev/null
sleep 2
CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 2 "http://127.0.0.1:$PORT/" || true)
if [ "$CODE" = "000" ]; then
  echo "port $PORT: down (connection refused)"
else
  echo "port $PORT: STILL UP (HTTP $CODE) — investigate"
fi
rm -f "/tmp/qa-desk-$PORT.db" "/tmp/qa-desk-$PORT.log" "/tmp/qa-roster-$PORT.csv"
