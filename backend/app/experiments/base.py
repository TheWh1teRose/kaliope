"""The experiment contract.

An experiment is a fixed, hand-coded test setup in the Experimentieren section.
Each one lives in its own module, registers itself, and owns three things: the
shape of its setup (what the user can change), the defaults that setup starts
from, and how one run turns a setup into model calls. Runs and collected outputs
of every experiment share the ``experiment_runs`` / ``experiment_outputs``
tables, so adding an experiment needs no migration and no new endpoint.

Nothing here creates a production run, a review note or a pipeline node.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Protocol

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.llm.base import CompletionRequest, Effort, LLMClient, LLMError, Message, ThinkingMode
from app.pipeline.framework.artifacts import ArtifactStore

#: ``{{name}}`` in a template; names are lower-case identifiers.
PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")
FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class TemplateError(ValueError):
    """A template references fields that have no value."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__(
            "placeholders without a field: " + ", ".join(f"{{{{{name}}}}}" for name in missing)
        )
        self.missing = missing


@dataclass(frozen=True)
class Rendered:
    text: str
    #: Fields the template never uses.
    unused: list[str]


def render(template: str, values: dict[str, str], *, strict: bool = True) -> Rendered:
    """Fill ``{{name}}`` placeholders.

    With ``strict`` a placeholder without a field is an error; without it the
    placeholder is left as typed. Single braces (JSON examples in prompts) are
    never touched.
    """
    used = {match.group(1) for match in PLACEHOLDER.finditer(template)}
    missing = sorted(name for name in used if name not in values)
    if strict and missing:
        raise TemplateError(missing)

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        return values[name] if name in values else match.group(0)

    return Rendered(
        text=PLACEHOLDER.sub(fill, template),
        unused=sorted(name for name in values if name not in used),
    )


# ----------------------------------------------------------------- settings


class ModelSettings(BaseModel):
    """What the shared model settings form edits (frontend ``ModelSettings``)."""

    model: str
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    top_k: int | None = Field(default=None, ge=1)
    thinking: ThinkingMode = "default"
    thinking_budget: int | None = Field(default=None, ge=0)
    effort: Effort | None = None
    max_tokens: int = Field(default=8000, ge=1)
    #: Ask for ``json_schema``-shaped output; off returns plain text.
    structured: bool = True
    cache_system: bool = True

    def request(
        self, *, system: str, user: str, json_schema: dict[str, Any] | None
    ) -> CompletionRequest:
        return CompletionRequest(
            model=self.model,
            system=system,
            messages=[Message(role="user", content=user)],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            thinking=self.thinking,
            thinking_budget=self.thinking_budget,
            effort=self.effort,
            json_schema=json_schema if self.structured else None,
            cache_system=self.cache_system,
        )


class PromptSetup(BaseModel):
    """A system prompt, a user template filled from named fields, and settings."""

    system_prompt: str
    user_template: str
    fields: dict[str, str] = Field(default_factory=dict)
    settings: ModelSettings
    json_schema: dict[str, Any] | None = None

    @field_validator("fields")
    @classmethod
    def _field_names(cls, value: dict[str, str]) -> dict[str, str]:
        bad = [name for name in value if not FIELD_NAME.match(name)]
        if bad:
            raise ValueError(
                "field names must be lower-case letters, digits and underscores: " + ", ".join(bad)
            )
        return value

    @field_validator("json_schema")
    @classmethod
    def _schema_is_object(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and value.get("type") != "object":
            raise ValueError('the JSON schema must describe an object ("type": "object")')
        return value


class FieldSpec(BaseModel):
    """Label and hint for one of an experiment's own fields."""

    key: str
    label: str
    multiline: bool = False
    hint: str | None = None


# ------------------------------------------------------------------- source


class SourceIn(BaseModel):
    """Where an experiment's fields were loaded from."""

    run_id: str
    beat_id: str | None = None


class BeatOut(BaseModel):
    id: str
    title: str
    word_budget: int
    passage_count: int


class SourceOut(BaseModel):
    fields: dict[str, str]
    beats: list[BeatOut]
    #: Provenance shown on the page and stored with every run.
    source: dict[str, Any]


@dataclass
class ExperimentContext:
    """What a run may need beyond its setup."""

    source: SourceIn | None
    store: ArtifactStore
    #: Short database reads; never held open across a model call.
    session_scope: Callable[[], AbstractContextManager[Session]]
    created_by: str | None = None


# ------------------------------------------------------------------- result


class ItemRef(BaseModel):
    """One collectable thing inside a run's output."""

    item: str = "main"
    title: str | None = None
    #: Copied into ``experiment_outputs.meta_json`` when the item is collected.
    meta: dict[str, Any] = Field(default_factory=dict)


class ExperimentResult(BaseModel):
    #: Experiment-defined; shown in the JSON view.
    output_json: dict[str, Any]
    text: str | None = None
    warnings: list[str] = Field(default_factory=list)


class Experiment(Protocol):
    key: ClassVar[str]
    title: ClassVar[str]
    summary: ClassVar[str]
    #: The pipeline step it targets, shown as the card's eyebrow.
    target: ClassVar[str]
    version: ClassVar[str]
    Setup: ClassVar[type[BaseModel]]

    def defaults(self) -> BaseModel: ...

    def field_specs(self) -> list[FieldSpec]: ...

    def extras(self) -> dict[str, Any]:
        """Texts or schemas the page shows that are not part of the setup."""
        ...

    def validate_setup(self, setup: BaseModel) -> list[str]:
        """Problems that refuse a run before it is queued; empty when it may run."""
        ...

    def run(self, setup: BaseModel, llm: LLMClient, ctx: ExperimentContext) -> ExperimentResult: ...

    def items(self, output_json: dict[str, Any]) -> list[ItemRef]: ...

    def load_source(
        self, session: Session, store: ArtifactStore, source: SourceIn
    ) -> SourceOut: ...


class PromptExperiment:
    """One model call: system prompt + rendered user template + settings.

    Subclasses set the class attributes and ``defaults``; ``load_source`` is
    optional and refuses by default.
    """

    key: ClassVar[str]
    title: ClassVar[str]
    summary: ClassVar[str]
    target: ClassVar[str]
    version: ClassVar[str]
    Setup: ClassVar[type[BaseModel]] = PromptSetup

    def defaults(self) -> BaseModel:  # pragma: no cover - every experiment overrides it
        raise NotImplementedError

    def field_specs(self) -> list[FieldSpec]:
        return []

    def extras(self) -> dict[str, Any]:
        return {}

    def validate_setup(self, setup: BaseModel) -> list[str]:
        assert isinstance(setup, PromptSetup)
        try:
            render(setup.user_template, setup.fields)
        except TemplateError as exc:
            return [str(exc)]
        if setup.settings.structured and setup.json_schema is None:
            return ["structured output needs a JSON schema"]
        return []

    def run(self, setup: BaseModel, llm: LLMClient, ctx: ExperimentContext) -> ExperimentResult:
        assert isinstance(setup, PromptSetup)
        rendered = render(setup.user_template, setup.fields)
        completion = llm.complete(
            setup.settings.request(
                system=setup.system_prompt, user=rendered.text, json_schema=setup.json_schema
            )
        )
        warnings = list(completion.warnings)
        if rendered.unused:
            warnings.append("fields not used by the template: " + ", ".join(rendered.unused))
        if completion.stop_reason == "max_tokens":
            warnings.append("the answer was cut off at max_tokens")
        payload: Any = None
        if setup.settings.structured:
            try:
                payload = completion.json_payload()
            except LLMError as exc:
                warnings.append(f"the answer was not valid JSON: {exc}")
        return ExperimentResult(
            output_json={
                "payload": payload,
                "text": completion.text,
                "stop_reason": completion.stop_reason,
            },
            text=completion.text,
            warnings=warnings,
        )

    def items(self, output_json: dict[str, Any]) -> list[ItemRef]:
        return [ItemRef(item="main")]

    def load_source(self, session: Session, store: ArtifactStore, source: SourceIn) -> SourceOut:
        raise LookupError(f"experiment '{self.key}' cannot load fields from a run")


#: Statuses an ``experiment_runs`` row moves through.
RunStatus = Literal["queued", "running", "completed", "failed"]
