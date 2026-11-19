# Evals

Evals turn "the answers got worse" into a failing build. A suite is a YAML file of prompts and
assertions; `switchboard eval run` sends them through a running gateway and reports a pass rate.

The format and every assertion type are documented in
[`examples/evals/README.md`](../examples/evals/README.md). This page covers how to use them.

## Write a suite worth running

- **Assert on behaviour, not wording.** `contains: "30 days"` survives rephrasing; `equals` on a
  whole paragraph doesn't. Use `regex` and `json_schema` for structure.
- **Use `llm_judge` for qualities you can't pattern-match**, such as tone, but keep the rubric
  specific ("offers account credit and does not promise a refund") and set a threshold. Pin the
  judge with `judge_model` so changing the model under test doesn't change the grader.
- **Mark the few behaviours that must never break as `critical`.** A failed critical case fails
  the run regardless of pass rate.
- **Run at `temperature: 0`** (the default) to reduce noise, and expect some remaining variance.

## Gate pull requests

The repository runs the keyless `smoke` suite on every PR (`.github/workflows/evals.yml`) to check
the gateway itself. For your own prompts, run a suite against a real model and compare with a
stored baseline:

```bash
# once, on a reviewed state of the prompt: save the baseline
switchboard eval run evals/support-bot.yaml --out baselines/
git add baselines/support-bot.json && git commit -m "evals: record baseline"

# in CI, on every change
switchboard eval run evals/support-bot.yaml \
  --baseline baselines/ \
  --max-regression 0.05 \
  --summary-md "$GITHUB_STEP_SUMMARY"
```

Exit code `1` means a critical case failed, the pass rate fell more than `--max-regression` below
the baseline, or (with `--min-pass-rate`) it is under an absolute floor. The Markdown summary
shows each case, what regressed and what was fixed.

## Store runs for the dashboard

Add `--report` and set `SWITCHBOARD_ADMIN_TOKEN` to upload each run. The dashboard's **Evals** page
then shows a pass-rate trend per suite and the per-case results, including model output and the
reason each failed assertion failed.

## Cost

Each case is one chat completion, plus one more per `llm_judge` assertion. Cost per case is shown
in the results (from the gateway's `x-switchboard-cost-usd` header) and every eval request appears
in the request log like any other.
