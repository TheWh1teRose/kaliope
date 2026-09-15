"""Shared fixtures and corpus discovery (§13.1).

Test PDFs live in ``tests/corpus/``. They are **discovered at runtime** — no
test names a file, so adding a PDF extends coverage with no test-code change,
and a document that breaks an invariant turns the suite red without anyone
writing a new test.

``tests/corpus/holdout/`` is not opened unless ``KALLIOPE_HOLDOUT=1``. That is
the only mechanism the suite has for detecting overfitting, so it must stay
closed during development.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

CORPUS_DIR = Path(__file__).parent / "corpus"
HOLDOUT_DIR = CORPUS_DIR / "holdout"
HOLDOUT_ENV = "KALLIOPE_HOLDOUT"
LLM_ENV = "KALLIOPE_LLM_TESTS"


@dataclass(frozen=True)
class CorpusDocument:
    path: Path
    holdout: bool

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def meta(self) -> dict[str, Any]:
        """Coarse facts only (§13.1). Never expected counts."""
        sidecar = self.path.with_suffix(".meta.yaml")
        if not sidecar.exists():
            return {}
        loaded = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}

    @property
    def family(self) -> str:
        return str(self.meta.get("family", "unlabelled"))

    def __str__(self) -> str:  # pragma: no cover - identifies the parameter
        return self.name


def holdout_enabled() -> bool:
    return os.environ.get(HOLDOUT_ENV, "").strip() not in {"", "0", "false", "no"}


def llm_tests_enabled() -> bool:
    return os.environ.get(LLM_ENV, "").strip() not in {"", "0", "false", "no"}


def discover_corpus(include_holdout: bool | None = None) -> list[CorpusDocument]:
    include = holdout_enabled() if include_holdout is None else include_holdout
    documents: list[CorpusDocument] = [
        CorpusDocument(path=path, holdout=False) for path in sorted(CORPUS_DIR.glob("*.pdf"))
    ]
    if include and HOLDOUT_DIR.exists():
        documents.extend(
            CorpusDocument(path=path, holdout=True) for path in sorted(HOLDOUT_DIR.glob("*.pdf"))
        )
    return documents


CORPUS = discover_corpus()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "corpus: parameterised over the discovered corpus")
    config.addinivalue_line("markers", "holdout: opens the holdout corpus")
    config.addinivalue_line("markers", "llm: requires live LLM credentials")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if llm_tests_enabled():
        return
    skip = pytest.mark.skip(reason=f"set {LLM_ENV}=1 to run tests that call a live model")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip)


# ------------------------------------------------------------------ fixtures


@pytest.fixture(scope="session", autouse=True)
def _isolated_settings(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Point the whole application at a throwaway /data for the session."""
    from app.config import Settings, set_settings
    from app.db import reset_engine

    data_dir = tmp_path_factory.mktemp("kalliope-data")
    settings = Settings(
        app_secret_key="test-secret-key-not-for-production",
        data_dir=data_dir,
        anthropic_api_key="",
        openai_api_key="",
        google_api_key="",
        testing=True,
    )
    set_settings(settings)
    settings.ensure_dirs()
    reset_engine()

    from app.db import create_all
    from app.pipeline.framework.registry import bootstrap_nodes

    bootstrap_nodes()
    create_all()

    yield settings

    set_settings(None)
    reset_engine()
    shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def corpus() -> list[CorpusDocument]:
    return CORPUS


def parsed_document(document: CorpusDocument, **overrides: Any) -> Any:
    """Parse one corpus document with zone classification disabled by default.

    The invariants are properties of the deterministic pipeline. Running them
    without a model keeps them fast, offline and reproducible; CAP-3 is where
    the classifier itself is measured.
    """
    from app.ingestion.pipeline import ParseOptions, parse

    options = ParseOptions(document_id=f"corpus-{document.name}", parse_version=1, **overrides)
    return parse(document.path, options, classifier=overrides.pop("classifier", None))


@pytest.fixture(scope="session")
def parses(corpus: list[CorpusDocument]) -> dict[str, Any]:
    """Parse every corpus document once and share the result across tests."""
    if not corpus:
        return {}
    return {document.name: parsed_document(document) for document in corpus}


def requires_corpus() -> Any:
    return pytest.mark.skipif(
        not CORPUS,
        reason=(
            "no PDFs in tests/corpus/. Drop documents in to activate the "
            "corpus-parameterised suite (§13.1)."
        ),
    )
