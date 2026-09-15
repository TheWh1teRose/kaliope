"""The ``ingest`` node (§6).

Parsing happens once per document version. When the API already parsed the
upload, the node loads that artifact instead of re-parsing; the result is
identical either way (INV-7), but re-parsing a large PDF inside every run would
be wasted work.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from app.config import get_settings
from app.ingestion.pipeline import ParseOptions, parse
from app.ingestion.zones import ZoneCache, ZoneClassifier
from app.llm import registry as llm_registry
from app.pipeline.framework.node import NodeContext, NodeError
from app.pipeline.framework.registry import register_node
from app.pipeline.framework.spec import NodeDoc, NodeParam
from app.schemas.document import ParsedDocument


class DocumentRef(BaseModel):
    """Everything the run knows about the source document."""

    document_id: str
    parse_version: int
    sha256: str
    #: Hash of a ``ParsedDocument`` artifact produced at upload time, if any.
    parsed_artifact_hash: str | None = None
    language_override: str | None = None


class IngestInput(BaseModel):
    document_ref: DocumentRef


class IngestNode:
    name = "ingest"
    title = "Dokument einlesen"
    version = "1.0"
    Input: type[BaseModel] = IngestInput
    Output: type[BaseModel] = ParsedDocument
    produces = "parsed"

    doc = NodeDoc(
        summary="Turns the PDF into ordered, zone-classified text blocks with page geometry.",
        detail=[
            "Reuses the parse produced when the document was uploaded whenever its artifact "
            "is still on disk. That is the normal path: parsing is per document version, not "
            "per run, so two runs over the same document read the identical text.",
            "When there is no stored parse it parses the file itself: extract text and "
            "geometry, rebuild the reading order, detect and drop repeated page furniture, "
            "recover the section tree from the PDF outline or from typography, and hand every "
            "block to the zone classifier.",
            "The zone classifier is the one model call in this step. It labels each block "
            "(body, heading, exercise, reference, …), which decides what may be narrated at "
            "all and how much weight a block carries downstream. Its answers are cached by "
            "block content, so re-parsing a document costs almost nothing the second time.",
            "Every block keeps its page rectangles. That is what later lets a citation in the "
            "finished script be drawn back onto the page image in the review view.",
        ],
        inputs={
            "document_ref": "Which document and parse version to read, plus the hash of the "
            "parse made at upload time and any language override.",
        },
        output="A ParsedDocument: blocks, sections, page sizes, stated objectives, the "
        "ingestion report, and the language.",
        failure_modes=[
            "The document has neither a stored parse nor a readable source file — the upload "
            "was removed from the data volume.",
            "The PDF has no extractable text layer (a scan without OCR). It parses, but the "
            "content budget downstream will refuse the run.",
        ],
        cost="One small-model call per uncached block group. Cheap, and cached across runs.",
    )

    params = [
        NodeParam(
            key="zone_model",
            label="Zone classifier model",
            type="model",
            description=(
                "Model used to label text blocks. A small, fast model is the right choice; "
                "the task is classification, not writing. Empty falls back to ZONE_MODEL "
                "from the environment."
            ),
            advanced=True,
        ),
    ]

    def run(self, inp: IngestInput, ctx: NodeContext) -> ParsedDocument:
        ref = inp.document_ref

        if ref.parsed_artifact_hash and ctx.artifacts.exists(ref.parsed_artifact_hash):
            ctx.progress("reusing the parse produced at upload time")
            return ParsedDocument.model_validate(ctx.artifacts.get_raw(ref.parsed_artifact_hash))

        if ctx.document_path is None or not Path(ctx.document_path).exists():
            raise NodeError(
                f"document {ref.document_id} has neither a stored parse nor a readable source file"
            )

        ctx.progress("parsing the document")
        return parse(
            Path(ctx.document_path),
            ParseOptions(
                document_id=ref.document_id,
                parse_version=ref.parse_version,
                language_override=ref.language_override,
            ),
            classifier=build_classifier(ctx),
        )


def build_classifier(ctx: NodeContext) -> ZoneClassifier:
    settings = get_settings()
    model = str(ctx.get("zone_model") or settings.zone_model or llm_registry.DEFAULT_SMALL_MODEL)
    return ZoneClassifier(
        client=ctx.llm,
        model=model,
        cache=ZoneCache(settings.data_dir / "zone_cache"),
    )


register_node(IngestNode())
