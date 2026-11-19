#!/usr/bin/env bash
# Measures the gateway's own overhead with ApacheBench against a zero-latency mock provider.
#
#   bash benchmarks/run.sh                       # defaults below
#   REQUESTS=50000 CONCURRENCY=128 WORKERS=8 bash benchmarks/run.sh
#
# Needs: ab (ApacheBench), uv, and a Redis on localhost:6379 for the cached scenario.
set -euo pipefail
cd "$(dirname "$0")/.."

REQUESTS=${REQUESTS:-30000}
CONCURRENCY=${CONCURRENCY:-64}
WORKERS=${WORKERS:-4}
PORT=${PORT:-8080}

command -v ab >/dev/null || { echo "ApacheBench (ab) is required" >&2; exit 1; }

work=$(mktemp -d)
server_pid=""
cleanup() {
  if [ -n "$server_pid" ]; then
    # `uv run` is only a wrapper: stop its children (the uvicorn workers) too.
    pkill -P "$server_pid" 2>/dev/null || true
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  rm -rf "$work"
}
trap cleanup EXIT

if curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
  echo "Something is already listening on port $PORT; stop it or set PORT." >&2
  exit 1
fi

# uncached: temperature > 0 bypasses the cache, so each request goes through routing and the
# provider. cached: a deterministic request, answered from the Redis exact cache after the first.
echo '{"model":"mock","temperature":0.7,"messages":[{"role":"user","content":"Summarise the plot of Hamlet in one sentence."}]}' > "$work/uncached.json"
echo '{"model":"mock","messages":[{"role":"user","content":"Summarise the plot of Hamlet in one sentence."}]}' > "$work/cached.json"

SWITCHBOARD_CONFIG_PATH=config/bench.yaml SWITCHBOARD_AUTH_ENABLED=false SWITCHBOARD_DATABASE_URL= \
  uv run uvicorn --factory switchboard.api.app:create_app \
  --workers "$WORKERS" --port "$PORT" --log-level warning > "$work/gateway.log" 2>&1 &
server_pid=$!

for _ in $(seq 1 30); do
  curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null && break
  sleep 1
done
curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null || { cat "$work/gateway.log"; exit 1; }

url="http://127.0.0.1:$PORT/v1/chat/completions"
run_ab() { # <requests> <concurrency> <payload name> <output file>
  ab -k -q -n "$1" -c "$2" -p "$work/$3.json" -T application/json "$url" > "$4" 2>&1
}

run_ab 1000 16 cached "$work/warmup.ab"  # warm connections, imports and the cache entry
: > "$work/gateway.log"                  # measure only what follows
run_ab "$REQUESTS" "$CONCURRENCY" uncached "$work/uncached.ab"
run_ab "$REQUESTS" "$CONCURRENCY" cached "$work/cached.ab"

echo "$REQUESTS requests per scenario, $CONCURRENCY concurrent, $WORKERS gateway workers"
echo
python3 benchmarks/summarise.py "$work/uncached.ab" "$work/cached.ab" "$work/gateway.log"
