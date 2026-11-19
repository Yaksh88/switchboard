from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from switchboard.evals.suite import Suite, load_suite

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "evals"


def suite_with(*cases: dict[str, Any], **extra: Any) -> Suite:
    return Suite.model_validate({"name": "s", "model": "m", "cases": list(cases), **extra})


def case(**overrides: Any) -> dict[str, Any]:
    return {"id": "c", "input": "hi", "assert": [{"type": "contains", "value": "x"}], **overrides}


@pytest.mark.parametrize("name", ["smoke.yaml", "support-bot.yaml"])
def test_example_suites_load(name: str) -> None:
    suite = load_suite(EXAMPLES / name)
    assert suite.cases and all(c.assertions for c in suite.cases)


def test_duplicate_case_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate case ids: c"):
        suite_with(case(), case())


def test_llm_judge_requires_rubric() -> None:
    with pytest.raises(ValidationError, match="needs a 'rubric'"):
        suite_with(case(**{"assert": [{"type": "llm_judge"}]}))


@pytest.mark.parametrize(
    "assertion",
    [
        {"type": "contains"},
        {"type": "json_schema"},
        {"type": "regex", "value": "("},
        {"type": "not-a-real-type", "value": "x"},
        {"type": "contains", "value": "x", "typo_field": 1},
    ],
)
def test_malformed_assertions_rejected(assertion: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        suite_with(case(**{"assert": [assertion]}))


def test_case_needs_exactly_one_of_input_or_messages() -> None:
    with pytest.raises(ValidationError, match="exactly one of"):
        suite_with(case(input=None))
    with pytest.raises(ValidationError, match="exactly one of"):
        suite_with(case(messages=[{"role": "user", "content": "hi"}]))


def test_case_needs_at_least_one_assertion() -> None:
    with pytest.raises(ValidationError):
        suite_with(case(**{"assert": []}))


def test_system_prompt_is_prepended_unless_the_case_brings_its_own() -> None:
    suite = suite_with(
        case(),
        case(
            id="own",
            input=None,
            messages=[{"role": "system", "content": "mine"}, {"role": "user", "content": "q"}],
        ),
        system="shared",
    )
    assert suite.messages_for(suite.cases[0]) == [
        {"role": "system", "content": "shared"},
        {"role": "user", "content": "hi"},
    ]
    assert suite.messages_for(suite.cases[1])[0]["content"] == "mine"


def test_defaults_apply_when_omitted(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(yaml.safe_dump({"name": "s", "model": "m", "cases": [case()]}))
    suite = load_suite(path)
    assert (suite.defaults.temperature, suite.defaults.max_tokens) == (0, 512)
