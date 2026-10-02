"""Content-addressed artifact store (§7.3).

Artifacts live at ``/data/artifacts/<sha256[:2]>/<sha256>.json``. They are never
mutated and never deleted by the application, which is what lets a re-run reuse
them and lets a failed run keep the artifacts it already produced (AC-FW-5).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def canonical_json(value: Any) -> str:
    """Stable serialisation. Key order is fixed so hashes are reproducible."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def hash_payload(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class StoredArtifact(BaseModel):
    hash: str
    kind: str
    size_bytes: int


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, digest: str) -> Path:
        return self.root / digest[:2] / f"{digest}.json"

    def exists(self, digest: str) -> bool:
        return self.path_for(digest).exists()

    def put(self, kind: str, model: BaseModel) -> StoredArtifact:
        payload = model.model_dump(mode="json")
        return self.put_raw(kind, payload)

    def put_raw(self, kind: str, payload: Any) -> StoredArtifact:
        body = canonical_json({"kind": kind, "payload": payload})
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        path = self.path_for(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename so a crash never leaves a half-written artifact
            # that a later run would read as valid.
            temporary = path.with_suffix(".tmp")
            temporary.write_text(body, encoding="utf-8")
            temporary.replace(path)
        return StoredArtifact(hash=digest, kind=kind, size_bytes=len(body.encode("utf-8")))

    def get_raw(self, digest: str) -> Any:
        path = self.path_for(digest)
        if not path.exists():
            raise KeyError(f"artifact {digest} is not in the store")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        return envelope["payload"]

    def get(self, digest: str, model_type: type[BaseModel]) -> Any:
        return model_type.model_validate(self.get_raw(digest))

    # ------------------------------------------------------------- steps

    def step_path(self, key: str) -> Path:
        return self.root / "steps" / key[:2] / f"{key}.ref"

    def get_step(self, key: str) -> str | None:
        """The artifact a node stored for one of its own steps, if it still exists.

        A step is a unit of work inside a node, such as one beat of the script,
        cached under a key the node computes itself. The runner's cache covers
        whole nodes; this lets a node reuse the steps whose inputs did not change.
        """
        path = self.step_path(key)
        if not path.exists():
            return None
        digest = path.read_text(encoding="utf-8").strip()
        return digest if digest and self.exists(digest) else None

    def put_step(self, key: str, digest: str) -> None:
        path = self.step_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(digest, encoding="utf-8")
        temporary.replace(path)

    def kind_of(self, digest: str) -> str:
        path = self.path_for(digest)
        if not path.exists():
            raise KeyError(f"artifact {digest} is not in the store")
        return str(json.loads(path.read_text(encoding="utf-8"))["kind"])
