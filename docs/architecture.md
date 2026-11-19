# Architecture

## Request lifecycle

A `POST /v1/chat/completions` passes through these stages, in order. Each is a small module with
one job.

1. **Authenticate** (`auth/dependencies.py`). The bearer token is hashed with SHA-256 and looked
   up in Postgres through an in-process cache (60 s). Unknown or revoked keys get a 401. With
   `SWITCHBOARD_AUTH_ENABLED=false` this stage is skipped.
2. **Admit** (`Gateway.admit`). Runs before any response bytes are sent, so a rejection is a real
   HTTP status and not an in-stream error:
   - the key's model allowlist (403),
   - the monthly budget, from month-to-date spend in the request log (429 `budget_exceeded`),
   - the requests-per-minute bucket, then the tokens-per-minute bucket (429 `rate_limit_exceeded`
     with `retry-after`).
3. **Cache lookup** (`Gateway._lookup`). Only deterministic requests are eligible: `temperature`
   unset or 0, and no `x-switchboard-cache: bypass` header. The exact cache is checked first, then
   the semantic cache if enabled. A hit is returned immediately (streams are replayed as chunks).
4. **Route** (`routing/router.py`). The alias's targets are tried in order. Each target gets
   `1 + max_retries` attempts on retryable errors (429, 5xx, timeouts) with exponential backoff;
   a non-retryable error such as a 400 is raised at once, because every other target would reject
   the same request. Targets whose circuit breaker is open are skipped.
5. **Store and settle.** A successful answer is written to the caches, and the tokens actually
   generated are charged against the key's TPM bucket.
6. **Account** (`Gateway.finish`). Cost is computed from the serving target's prices (zero for a
   cache hit), metrics are recorded, one JSON access-log line is written, and a row is queued for
   Postgres. This runs in a `finally`, so failed requests and client disconnects (status 499) are
   recorded too.

## Components and storage

| Component | Stores | Used for |
| --- | --- | --- |
| Redis | rate-limit buckets, exact and semantic caches | everything on the hot path |
| Postgres | `api_keys`, `request_logs`, `eval_runs` | key lookup, request history, stats, budgets, eval history |
| In process | circuit breakers, key lookup cache, budget cache | decisions that must be fast |

Postgres is never on the request path except for the first lookup of a key and the budget check,
both of which are cached for seconds. Request logs reach it through a background writer.

## Streaming

Streaming responses are server-sent events in the OpenAI chunk format. Because HTTP headers are
sent with the first byte, three things behave differently from non-streaming requests:

- **Fallback and retry happen only before the first chunk.** After text has reached the client,
  retrying elsewhere would duplicate it, so an upstream failure mid-stream is reported in-band as
  an `error` event followed by `[DONE]`.
- **Admission still happens first**, so rate-limit, budget and auth failures are proper status
  codes even for streams.
- **Partial answers are never cached.** The cache write happens only after the stream completes;
  a client disconnect cancels the generator before that point and the row is logged as 499.

## Failure behaviour

| Failure | What happens |
| --- | --- |
| Redis unreachable | Cache lookups and writes are skipped (logged); rate limits fail open and the request is served |
| Postgres unreachable | Keys already seen keep working until their 60 s cache expires; budget checks fail open; log rows are dropped and counted |
| Log queue full | Rows are dropped, never blocking requests; counted in `switchboard_log_dropped_total` |
| Upstream 429 or 5xx | Retry, then fall back to the next target; repeated failures open the circuit |
| All targets unavailable | 503 `no healthy targets for '<alias>'` |
| Auth backend failure | Requests are rejected: unlike limits, auth fails closed |

## Observability

- `/metrics` exposes Prometheus counters and histograms for requests, latency, time to first token,
  tokens, cost, fallbacks, circuit-breaker state and dropped log rows.
- Each request writes one JSON line to stdout (`switchboard.access`) with the request id, model,
  provider, status, latency, cache status, attempts, tokens and cost.
- The admin API under `/admin/api` backs the dashboard: overview with a zero-filled time series,
  per-model breakdown, keyset-paginated request explorer, provider health and eval runs.
