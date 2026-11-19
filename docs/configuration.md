# Configuration

Switchboard has two layers of configuration: environment variables for the process, and a YAML
file for providers, models and caching.

## Environment variables

All variables use the `SWITCHBOARD_` prefix and can also be set in a `.env` file.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CONFIG_PATH` | `config/switchboard.yaml` | Path to the YAML config |
| `HOST` / `PORT` | `0.0.0.0` / `8080` | Listen address |
| `LOG_LEVEL` | `info` | Level for the `switchboard` logger |
| `DATABASE_URL` | `postgresql+asyncpg://localhost/switchboard` | Postgres for keys, request logs and eval runs. Set it empty to run without a database |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis for caches and rate limits |
| `AUTH_ENABLED` | `true` | Require a virtual API key on `/v1`. Needs `DATABASE_URL` |
| `ADMIN_TOKEN` | unset | Bearer token for `/admin` and the dashboard. Unset disables the admin API |
| `DASHBOARD_DIR` | `dashboard/dist` | Built dashboard, served at `/dashboard/` when it exists |

Without a database there are no virtual keys, no request log, and no admin API, but the gateway
routes, caches and rate-limits as usual.

## YAML config

`${VAR}` and `${VAR:-default}` are expanded from the environment, so secrets never live in the file.

### `providers`

```yaml
providers:
  openai:
    type: openai          # openai | anthropic | mock
    api_key: ${OPENAI_API_KEY}
    base_url: https://api.openai.com/v1   # optional; set for any OpenAI-compatible server
    timeout_s: 60
    max_retries: 1        # extra attempts per target on retryable errors (0 to 5)
```

`type: openai` works with any server speaking the OpenAI API (vLLM, Ollama, Together, Groq): set
`base_url`. `type: mock` is an in-process provider that echoes the last user message; it takes
`latency_ms`, `chunk_delay_ms` and `failure_rate` for demos and chaos testing.

### `models`

```yaml
models:
  resilient:                # the alias clients send as "model"
    targets:                # tried in order
      - provider: anthropic
        model: claude-haiku-4-5
        input_cost_per_mtok: 1.00    # USD per million tokens, for cost tracking and budgets
        output_cost_per_mtok: 5.00
      - { provider: openai, model: gpt-4o-mini }
```

Responses report the alias, never the upstream model name.

### `cache`

```yaml
cache:
  enabled: true
  ttl_s: 3600
  semantic:
    enabled: false
    threshold: 0.92        # cosine similarity needed for a hit
    max_entries: 1000      # per model and conversation context; oldest evicted first
    embedder: hashing      # hashing | openai
    embedding_provider: openai          # required when embedder is openai
    embedding_model: text-embedding-3-small
```

Only requests with `temperature` unset or 0 are cached, and errors are never cached. The semantic
cache compares only the final user message; the system prompt and earlier turns must match
exactly. The default `hashing` embedder is local and free but matches rewordings that share most
of their words, not true paraphrases; use `openai` embeddings for those.

## Endpoints

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `POST /v1/chat/completions` | API key | Chat completions, streaming or not |
| `GET /v1/models` | API key | Configured aliases |
| `GET /healthz` | none | Liveness |
| `GET /metrics` | none | Prometheus metrics |
| `POST /admin/keys`, `GET /admin/keys`, `DELETE /admin/keys/{id}` | admin token | Manage API keys |
| `GET /admin/api/overview`, `/models`, `/requests`, `/requests/{id}`, `/providers` | admin token | Stats behind the dashboard |
| `POST /admin/api/evals`, `GET /admin/api/evals[/{id}]` | admin token | Store and read eval runs |
| `/dashboard/` | none (the API calls need the admin token) | Admin UI |

Errors use the OpenAI shape: `{"error": {"message": ..., "type": ..., "code": ...}}`.

## API keys

```bash
switchboard keys create --name checkout-service --rpm 120 --tpm 200000 --budget 50 --model gpt-4o-mini
switchboard keys list
switchboard keys revoke <id>
```

The secret is shown once; only its SHA-256 is stored. Limits are optional. `--budget` is a
monthly cap in USD computed from logged spend (it can overshoot by what a key spends within one
30-second cache window). A token-per-minute limit charges an estimated prompt size (about four
characters per token) up front and the real generated tokens afterwards.

## CLI

| Command | Does |
| --- | --- |
| `switchboard serve` | Run the gateway |
| `switchboard db upgrade` | Apply database migrations |
| `switchboard keys create\|list\|revoke` | Manage API keys |
| `switchboard eval run SUITE...` | Run eval suites (see [evals.md](evals.md)) |
