"""Corpus coverage summary (§13.1).

CI prints this so that a green suite is read alongside what it was run over. A
suite that passes on two documents from one family is not the same result as
one that passes on twelve from five, and the summary makes the difference
visible instead of implied.
"""

from __future__ import annotations

from collections import Counter

from tests.conftest import CORPUS, discover_corpus, holdout_enabled


def test_corpus_coverage_summary(parses: dict) -> None:
    documents = CORPUS
    holdout = discover_corpus(include_holdout=True)
    holdout_count = len(holdout) - len(discover_corpus(include_holdout=False))

    lines: list[str] = ["", "Corpus coverage", "=" * 64]

    if not documents:
        lines.append("No PDFs in tests/corpus/.")
        lines.append("The invariant and capability suites are skipped until one is added.")
        if holdout_count:
            lines.append(f"{holdout_count} holdout document(s) present but not opened.")
        print("\n".join(lines))
        return

    families = Counter(d.family for d in documents)
    languages = Counter(parses[d.name].language for d in documents)
    structure = Counter(parses[d.name].report.structure_source for d in documents)
    confidence = Counter(parses[d.name].report.ingestion_confidence for d in documents)
    pages = [parses[d.name].page_count for d in documents]
    columns = Counter()
    for document in documents:
        report = parses[document.name].report
        columns["tables" if report.table_count else "no tables"] += 1

    lines += [
        f"documents          {len(documents)}"
        + (f"  (+{holdout_count} holdout, opened)" if holdout_enabled() and holdout_count else ""),
        f"families           {len(families)}  {dict(families)}",
        f"languages          {dict(languages)}",
        f"pages              min {min(pages)}  median {sorted(pages)[len(pages) // 2]}  "
        f"max {max(pages)}  total {sum(pages)}",
        f"structure source   {dict(structure)}",
        f"ingestion conf.    {dict(confidence)}",
        f"tables             {dict(columns)}",
        "-" * 64,
    ]

    if not holdout_enabled() and holdout_count:
        lines.append(
            f"{holdout_count} holdout document(s) were NOT opened. "
            "Run `make test-holdout` for the final acceptance pass (§13.5)."
        )
    if len(families) < 3:
        lines.append(
            "Fewer than three distinct families. The suite cannot tell "
            "generality from overfitting at this breadth."
        )

    print("\n".join(lines))
