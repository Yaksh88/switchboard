# Eval suites

Suites are YAML files that send prompts through the gateway and assert on the replies, so a
prompt or model change that makes answers worse fails a build instead of reaching users.

| Suite | What it is for |
| --- | --- |
| `smoke.yaml` | Checks the gateway itself against the built-in `mock` model. No API keys; runs in CI. |
| `support-bot.yaml` | A realistic customer-support regression suite for a real model. |

```bash
# gateway running locally (see the main README)
switchboard eval run examples/evals/smoke.yaml --min-pass-rate 1.0

# a real model, saving results so the next run can be compared against them
switchboard eval run examples/evals/support-bot.yaml --model gpt-4o-mini --out baselines/
switchboard eval run examples/evals/support-bot.yaml --model gpt-4o-mini --baseline baselines/
```

## Format

```yaml
name: support-bot            # used for result files and baseline lookup
model: gpt-4o-mini           # any alias the gateway serves; --model overrides it
system: You are a support agent.   # optional, prepended to every case
defaults: { temperature: 0, max_tokens: 512 }
cases:
  - id: refund-window        # unique within the suite
    input: How long do I have to return an item?     # or `messages:` for multi-turn
    critical: true           # a failure here always fails the run
    assert:
      - { type: contains, value: "30 days" }
```

## Assertions

| `type` | Passes when |
| --- | --- |
| `equals` | the reply equals `value` (trimmed) |
| `contains` / `not_contains` | the reply contains (or lacks) `value`, which may be a list |
| `regex` | `value` matches somewhere in the reply |
| `json_valid` | the reply parses as JSON (markdown fences are tolerated) |
| `json_schema` | the reply is JSON matching `schema`; failures name the offending path |
| `max_latency_ms` | the gateway answered within `value` milliseconds |
| `llm_judge` | a judge model scores the reply against `rubric` at or above `threshold` (default 0.7) |

Text comparisons ignore case unless `case_sensitive: true`. `llm_judge` calls go through the
same gateway, using the suite's model unless `judge_model` is set.

## Exit codes

`switchboard eval run` exits `1` when any suite:

- fails a `critical` case,
- drops more than `--max-regression` (default 5 points) below its `--baseline`, or
- scores under `--min-pass-rate`.

Otherwise it exits `0`. Use `--summary-md "$GITHUB_STEP_SUMMARY"` to put a results table on a
CI job page and `--report` (with `SWITCHBOARD_ADMIN_TOKEN`) to store runs for the dashboard.
