# Design decisions

The choices that shaped Switchboard, with what each costs. Newest context first inside each entry:
the problem, the decision, the trade-off.

## 1. The public API is the OpenAI wire format

**Problem.** Clients should be able to adopt a gateway without rewriting code, and providers each
speak a different dialect.

**Decision.** The gateway exposes the OpenAI Chat Completions format and translates to each
provider behind it (`providers/anthropic.py` maps system prompts, turn merging, temperature range,
stop sequences and streaming events). Model aliases hide upstream names.

**Trade-off.** The OpenAI format becomes the lowest common denominator. Provider-specific features
(Anthropic content blocks, tool use) need explicit translation work and aren't covered yet.

## 2. Fallback happens only before the first streamed chunk

**Problem.** A provider can fail halfway through a streamed answer.

**Decision.** Retries and fallback are allowed until the first chunk is yielded. After that, the
error is reported in-band and the stream ends. The partial answer is not cached.

**Trade-off.** A mid-stream failure is visible to the client. The alternative, silently restarting
on another provider, would repeat or contradict text the user has already read.

## 3. Rate limits are token buckets in a Lua script

**Problem.** Limits must hold across gateway processes and under concurrency.

**Decision.** One Lua script refills and takes tokens atomically in Redis. A test fires 50
concurrent requests at a bucket of 10 and asserts exactly 10 are admitted. Token limits charge an
estimate up front and the actual completion tokens afterwards (the bucket may go negative).

**Trade-off.** A token bucket allows bursts up to its capacity, unlike a sliding window, and the
prompt estimate (about four characters per token) is rough. Both are acceptable for protecting
budgets and providers; they are not billing-grade.

## 4. Limits and cache fail open; auth fails closed

**Problem.** Redis or Postgres will sometimes be down.

**Decision.** If Redis is unavailable, caching is skipped and rate limits admit the request. If
the budget check can't reach Postgres it admits the request. Authentication does not degrade: with
no way to verify a key, the request is rejected.

**Trade-off.** During a Redis outage limits aren't enforced, so spend can rise. Availability of
the LLM path was judged worth more than strict enforcement for a few minutes.

## 5. The semantic cache defaults to a local hashing embedder

**Problem.** Semantic caching needs embeddings, which usually mean another paid API call on every
request.

**Decision.** The default embedder hashes word unigrams and bigrams into a 512-dimension vector.
It is free and adds microseconds. An OpenAI-compatible embedder is available for real paraphrase
matching.

**Trade-off.** The hashing embedder matches rewordings that share most of their words ("how do I
reset my password" and "how can I reset my password"), not meaning. The default threshold (0.92) is
deliberately strict so it avoids wrong answers rather than chasing hit rate.

## 6. Request logs are written by a batching background task that drops when full

**Problem.** Writing a Postgres row per request on the request path would couple latency and
availability to the database.

**Decision.** `LogWriter.submit` snapshots the row (so latency is measured at completion) and puts
it on a bounded queue without blocking. A background task inserts batches of up to 200 every
second. On shutdown the queue is drained. If the queue is full, rows are dropped and counted in
`switchboard_log_dropped_total`.

**Trade-off.** Under sustained overload, history has gaps, and a crash loses up to a second of
rows. Budgets read this table, so they inherit the delay (and a 30-second cache).

## 7. Circuit breakers live in process memory

**Problem.** Repeatedly sending traffic to a failing provider wastes latency and retries.

**Decision.** Each target has a breaker that opens after five consecutive failures, stays open for
30 seconds, then lets exactly one probe through.

**Trade-off.** State isn't shared across replicas, so each learns independently (a few failed
requests per replica). Sharing it through Redis would add a network hop to every routing decision;
it is on the roadmap rather than assumed.

## 8. The eval harness talks to the gateway over HTTP

**Problem.** Evals should test what users get, not a code path that only exists in tests.

**Decision.** `switchboard eval run` is an ordinary client of a running gateway: the same routing,
caching, auth and cost accounting apply, and results can be pushed back for the dashboard.

**Trade-off.** Evals need a running gateway and spend real tokens. In exchange they exercise the
full stack, and `llm_judge` calls go through the same gateway and are accounted like any other.

## 9. Results are compared against a baseline, with critical cases

**Problem.** A pass-rate threshold alone either flaps on noisy LLM output or hides a break in the
one behaviour that must never regress.

**Decision.** A run fails if the pass rate drops more than a configured amount below a stored
baseline, or if any case marked `critical` fails, or if it falls below an optional absolute floor.

**Trade-off.** Baselines have to be stored and refreshed deliberately (for example committed after
a reviewed prompt change). That is the point: improving the baseline is a decision, not an accident.
