# Benchmarks

How much does putting the gateway in front of a model cost? `run.sh` answers that with a
zero-latency mock provider, so what's left is the gateway itself: routing, rate-limit and cache
lookups, accounting and logging.

```bash
bash benchmarks/run.sh                                         # defaults
REQUESTS=50000 CONCURRENCY=128 WORKERS=8 bash benchmarks/run.sh
```

It needs [ApacheBench](https://httpd.apache.org/docs/current/programs/ab.html) (`ab`, preinstalled
on macOS), `uv`, and a Redis on `localhost:6379` for the cached scenario. The script starts the
gateway with `config/bench.yaml`, runs both scenarios, and prints one table.

- **uncached**: `temperature: 0.7` bypasses the cache, so every request is routed to the mock provider.
- **cached**: a deterministic request, answered from the Redis exact cache after the first call.

Auth is off and there is no Postgres, so request logs aren't written. The JSON access log is on
(it is by default) and goes to a file.

## Results

Apple M1, 8 cores, 8 GB RAM, macOS. Python 3.12, uvicorn with 4 workers, Redis 8 on the same
machine. 30,000 requests per scenario, 64 concurrent connections with keep-alive.

| Scenario | Requests/s | Client p50 | p95 | p99 | In-gateway p50 | p95 | p99 | Failed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| uncached | 9,760 | 6 ms | 10 ms | 23 ms | <0.1 ms | 0.1 ms | 0.2 ms | 0 |
| cached | 6,384 | 8 ms | 17 ms | 33 ms | 0.8 ms | 6.2 ms | 24.7 ms | 0 |

That table is one run. Across six runs the uncached scenario ranged from 6,400 to 10,500
requests/s and the cached one from 5,200 to 7,400. The spread comes from running the load
generator, the gateway and Redis together on one 8 GB laptop, so treat the figures as an order of
magnitude, not a guarantee.

How to read the columns:

- **Client** latency is what `ab` saw. It includes the load generator competing for the same CPUs,
  so it overstates what a real client would see.
- **In-gateway** latency is the gateway's own timing for each request, read from its access log
  (resolution 0.1 ms). This is the overhead the gateway adds on top of the provider's time.
- A cached request is slower inside the gateway than an uncached one only because it waits on a
  Redis round trip; the mock provider answers instantly. Against a real model, which takes
  hundreds of milliseconds to seconds, a cache hit is faster by orders of magnitude.

## What this doesn't measure

- Real provider latency, which dominates any real request.
- Postgres request logging. Rows are written by a background batch writer and never block the
  request path, but a sustained rate beyond Postgres's insert throughput fills the queue and
  drops rows (counted in `switchboard_log_dropped_total`).
- Multi-machine deployments or Redis over a network.
