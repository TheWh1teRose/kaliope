# Test corpus

Drop PDFs in this directory. The suite **discovers** them at runtime — no test
names a file, so adding a document extends coverage with zero test-code
changes, and a document that breaks an invariant turns the suite red without
anyone writing a new test (§13.1).

```
tests/corpus/
├─ some-document.pdf
├─ some-document.meta.yaml     # optional, coarse facts only
└─ holdout/
   └─ never-opened-yet.pdf
```

## Optional sidecar

A `<name>.meta.yaml` next to a PDF may declare **only** coarse facts. It must
not contain expected counts — an expected count is how a suite becomes a
regression test for one document instead of a test of the pipeline.

```yaml
language: de          # expected document language
family: therapy       # free-text label, used for diversity reporting only
has_outline: true     # true | false | unknown
```

## Holdout

`holdout/` holds documents that must **not** be opened, inspected or debugged
against during implementation. They run only in the final acceptance pass:

```sh
make test-holdout
```

This is the only mechanism the suite has for detecting overfitting. A holdout
failure is a design finding, not a test to be adjusted: fix the generic
pipeline, never special-case the document.

## Zone labels (CAP-3)

`zone_labels.json` holds the only manual labelling in the suite:

```json
{
  "document-name": { "b000004": "exercise", "b000005": "body" }
}
```

It must cover at least 100 blocks across at least 4 documents from at least 3
families — spread, not concentrated — or the macro-F1 number means nothing.
CAP-3 skips with an explanation until the file exists.
