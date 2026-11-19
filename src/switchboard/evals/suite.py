"""Eval suite definition and loading.

A suite is a YAML file: a model, an optional system prompt, and cases that each send a
prompt and assert things about the reply::

    name: support-bot
    model: gpt-4o-mini
    system: You are a support agent.
    defaults: {temperature: 0, max_tokens: 512}
    cases:
      - id: refund-window
        input: How long do I have to return an item?
        critical: true
        assert:
          - {type: contains, value: "30 days"}
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

AssertionType = Literal[
    "equals",
    "contains",
    "not_contains",
    "regex",
    "json_valid",
    "json_schema",
    "max_latency_ms",
    "llm_judge",
]
NEEDS_VALUE = {"equals", "contains", "not_contains", "regex", "max_latency_ms"}


class Assertion(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: AssertionType
    value: Any = None
    case_sensitive: bool = False
    json_schema: dict[str, Any] | None = Field(default=None, alias="schema")
    rubric: str | None = None
    threshold: float = Field(default=0.7, ge=0, le=1)
    judge_model: str | None = None

    @model_validator(mode="after")
    def _has_what_its_type_needs(self) -> Self:
        if self.type in NEEDS_VALUE and self.value is None:
            raise ValueError(f"'{self.type}' needs a 'value'")
        if self.type == "regex":
            try:
                re.compile(str(self.value))
            except re.error as exc:
                raise ValueError(f"invalid regex {self.value!r}: {exc}") from exc
        if self.type == "json_schema" and self.json_schema is None:
            raise ValueError("'json_schema' needs a 'schema'")
        if self.type == "llm_judge" and not self.rubric:
            raise ValueError("'llm_judge' needs a 'rubric'")
        return self


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1)
    input: str | None = None
    messages: list[dict[str, Any]] | None = None
    critical: bool = False
    assertions: list[Assertion] = Field(alias="assert", min_length=1)

    @model_validator(mode="after")
    def _input_xor_messages(self) -> Self:
        if (self.input is None) == (self.messages is None):
            raise ValueError(f"case '{self.id}' needs exactly one of 'input' or 'messages'")
        return self

    def prompt_text(self) -> str:
        """The user-facing prompt, as shown to the judge."""
        if self.input is not None:
            return self.input
        users = [str(m.get("content", "")) for m in self.messages or [] if m.get("role") == "user"]
        return users[-1] if users else ""


class Defaults(BaseModel):
    temperature: float | None = 0
    max_tokens: int | None = 512


class Suite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    model: str
    system: str | None = None
    defaults: Defaults = Defaults()
    cases: list[EvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_case_ids(self) -> Self:
        ids = [c.id for c in self.cases]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate case ids: {', '.join(duplicates)}")
        return self

    def messages_for(self, case: EvalCase) -> list[dict[str, Any]]:
        messages = (
            list(case.messages)
            if case.messages is not None
            else [{"role": "user", "content": case.input}]
        )
        if self.system and not any(m.get("role") == "system" for m in messages):
            messages.insert(0, {"role": "system", "content": self.system})
        return messages


def load_suite(path: Path) -> Suite:
    return Suite.model_validate(yaml.safe_load(path.read_text()))
