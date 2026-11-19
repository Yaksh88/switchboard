# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-19

First release.

### Added

- OpenAI-compatible `/v1/chat/completions` (streaming and non-streaming) and `/v1/models`, with
  provider adapters for OpenAI (and any OpenAI-compatible server), Anthropic and a mock provider.
- Routing with ordered fallback targets, retries with exponential backoff, and per-target circuit
  breakers. Fallback happens only before the first streamed chunk.
- Redis exact cache and an optional embedding-based semantic cache, with streaming replay.
- Virtual API keys stored as SHA-256 hashes, with model allowlists, per-minute request and token
  limits (atomic Redis token buckets), and monthly spend budgets.
- Cost tracking from per-target token prices, a Postgres request log written by a batching
  background task, Prometheus metrics, and JSON access logs.
- Admin API and CLI (`serve`, `db upgrade`, `keys`, `eval`).
- Eval harness: YAML suites, eight assertion types including LLM-as-judge, baseline comparison,
  critical cases, Markdown summaries, and stored run history.
- React dashboard: traffic, request explorer, eval trends and a provider patch panel, in light
  and dark themes.
- Docker image, Compose stack with an optional Prometheus and Grafana profile, GitHub Actions
  workflows, and a gateway overhead benchmark.

### Known limitations

- The Anthropic adapter translates text chat only (no tool calls or images).
- Circuit breaker state is per process.
