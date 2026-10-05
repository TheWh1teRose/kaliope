"""Public bearer access, frozen script/audio pairing and atomic owner sharing."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.sharing import _invalid_attempts
from app.config import get_settings
from app.db import session_scope
from app.main import create_app
from app.models import (
    AudioTake,
    Document,
    EditEvent,
    ReviewFeedback,
    ReviewLink,
    ReviewSession,
    Run,
    RunNode,
    Series,
    User,
)
from app.pipeline.framework.artifacts import ArtifactStore
from app.schemas.audio import AudioMix
from app.schemas.document import Anchor, Block, IngestionReport, ParsedDocument
from app.schemas.pipeline import EpisodePlan, Script, Segment, SeriesBudget, SeriesPlan
from app.security import hash_password
from app.sharing_feedback import _write_attempts


@pytest.fixture
def owner(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(get_settings(), "review_public_origin", "https://review.example.test")
    _invalid_attempts.clear()
    email = f"{uuid4().hex}@example.test"
    with TestClient(create_app()) as client:
        with session_scope() as db:
            db.add(
                User(
                    email=email,
                    name="Owner",
                    role="admin",
                    password_hash=hash_password("test-password"),
                )
            )
        assert (
            client.post(
                "/api/auth/login", json={"email": email, "password": "test-password"}
            ).status_code
            == 200
        )
        yield client


def store() -> ArtifactStore:
    return ArtifactStore(get_settings().artifacts_dir)


def script(text: str = "Original recorded text") -> Script:
    return Script(
        segments=[
            Segment(
                id="seg-private", speaker="Host", text=text, kind="pedagogy", beat_id="beat-private"
            )
        ]
    )


def run(series_id: str | None = None, index: int | None = None) -> str:
    with session_scope() as db:
        document = Document(
            filename="private-source.pdf", sha256=uuid4().hex * 2, title="Private document title"
        )
        db.add(document)
        db.flush()
        row = Run(
            document_id=document.id,
            flow_id="baseline_v0",
            flow_version="1.0",
            status="completed",
            name="Private run name",
            series_id=series_id,
            episode_index=index,
        )
        db.add(row)
        db.flush()
        db.add(
            RunNode(
                run_id=row.id,
                node_name="script",
                node_version="0",
                cache_key=uuid4().hex * 2,
                artifact_hash=store().put("script", script()).hash,
            )
        )
        return row.id


def take(run_id: str, *, scope: str = "full", status: str = "completed") -> tuple[str, str]:
    blob = store().put_blob(b"ID3-test-podcast-bytes", ".mp3")
    mix = AudioMix(blob=blob, duration_s=12, gap_s=0.3, loudness_lufs=-16, chunk_offsets_s=[0])
    with session_scope() as db:
        row = AudioTake(
            run_id=run_id,
            flow_id="elevenlabs_dialog_v0",
            flow_version="1.0",
            status=status,
            request_json={"scope": scope},
            voice_cast_json={"private_voice": "secret"},
            script_hash=store().put("script_reviewed", script()).hash,
            manifest_json={"bag_hashes": {"audio_mix": store().put("audio_mix", mix).hash}},
        )
        db.add(row)
        db.flush()
        return row.id, blob


def choice(take_id: str | None = None) -> dict[str, Any]:
    return {
        "title": "Public title",
        "episodes": [{"index": 1, "title": "Public episode", "take_id": take_id}],
        "acknowledge_missing_audio": take_id is None,
    }


def share(owner: TestClient, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    base = f"/api/runs/{run_id}/review-link"
    preview = owner.post(base + "/preview", json=payload)
    assert preview.status_code == 200, preview.text
    response = owner.post(base, json={**payload, "preview_key": preview.json()["key"]})
    assert response.status_code == 201, response.text
    return response.json()


def public_path(created: dict[str, Any]) -> str:
    return "/api/public/review-links/" + created["url"].rsplit("/", 1)[1]


def test_audio_share_uses_recorded_script_and_allowlists_output(owner: TestClient) -> None:
    rid = run()
    tid, _ = take(rid)
    with session_scope() as db:
        user = db.scalars(select(User).order_by(User.created_at.desc())).first()
        assert user
        db.add(
            EditEvent(
                run_id=rid,
                target_type="segment",
                target_id="seg-private",
                user_id=user.id,
                action="edit",
                text_after="New private draft",
            )
        )
        before = db.scalar(select(func.count()).select_from(ReviewSession))
    options = owner.get(f"/api/runs/{rid}/review-link/options").json()
    assert options["episodes"][0]["takes"][0]["older_script"] is True
    created = share(owner, rid, choice(tid))
    path = public_path(created)
    owner.cookies.clear()
    body = owner.get(path)
    assert body.status_code == 200
    data = body.json()
    assert set(data) == {"title", "created_at", "expires_at", "episodes"}
    episode = data["episodes"][0]
    assert set(episode) == {"index", "title", "segments", "audio", "pages"}
    assert episode["pages"] == []
    assert episode["segments"] == [
        {
            "ordinal": 0,
            "speaker": "Host",
            "text": "Original recorded text",
            "citations": [],
        }
    ]
    assert set(episode["audio"]) == {"url", "duration_s"}
    audio = owner.get(episode["audio"]["url"])
    assert audio.content == b"ID3-test-podcast-bytes"
    partial = owner.get(episode["audio"]["url"], headers={"Range": "bytes=4-9"})
    assert partial.status_code == 206 and partial.content == b"test-p"
    assert partial.headers["content-range"] == "bytes 4-9/22"
    for result in [body, audio, partial]:
        assert result.headers["cache-control"] == "private, no-store"
        assert result.headers["referrer-policy"] == "no-referrer"
        assert result.headers["x-robots-tag"] == "noindex, nofollow"
    assert owner.get(f"/api/runs/{rid}/script").status_code == 401
    assert owner.get(f"/api/audio/takes/{tid}/mix").status_code == 401
    with session_scope() as db:
        assert db.get(Run, rid).status == "completed"
        assert db.scalar(select(func.count()).select_from(ReviewSession)) == before
        grant = db.get(ReviewLink, created["link"]["id"])
        assert (
            grant
            and grant.token_digest
            == hashlib.sha256(created["url"].rsplit("/", 1)[1].encode()).hexdigest()
        )
        assert timedelta(days=30) == grant.expires_at - grant.created_at


def test_acknowledged_script_only_snapshot_never_follows_edits_or_audio(owner: TestClient) -> None:
    rid = run()
    base = f"/api/runs/{rid}/review-link"
    assert (
        owner.post(
            base + "/preview", json={**choice(), "acknowledge_missing_audio": False}
        ).status_code
        == 409
    )
    created = share(owner, rid, choice())
    before = owner.get(public_path(created)).json()
    take(rid)
    with session_scope() as db:
        node = db.scalars(select(RunNode).where(RunNode.run_id == rid)).one()
        node.artifact_hash = store().put("script", script("Changed later")).hash
        db.get(Run, rid).name = "Changed title"
    assert owner.get(public_path(created)).json() == before
    assert before["episodes"][0]["audio"] is None
    assert owner.get(public_path(created) + "/episodes/1/audio").status_code == 404


def test_replacement_is_explicit_atomic_and_revokes_audio(owner: TestClient) -> None:
    rid = run()
    tid, blob = take(rid)
    base = f"/api/runs/{rid}/review-link"
    created = share(owner, rid, choice(tid))
    path = public_path(created)
    prepared = owner.post(base + "/preview", json=choice(tid)).json()
    assert owner.post(base, json={**choice(tid), "preview_key": prepared["key"]}).status_code == 409
    media = store().blob_path(blob, ".mp3")
    saved = media.read_bytes()
    media.unlink()
    replacement = {
        **choice(tid),
        "preview_key": prepared["key"],
        "replace_link_id": created["link"]["id"],
    }
    assert owner.post(base, json=replacement).status_code == 409
    assert owner.get(path).status_code == 200
    media.write_bytes(saved)
    new = owner.post(base, json=replacement)
    assert new.status_code == 201 and new.json()["url"] != created["url"]
    assert owner.get(path).status_code == 404
    assert owner.get(path + "/episodes/1/audio", headers={"Range": "bytes=0-4"}).status_code == 404
    assert owner.get(public_path(new.json())).status_code == 200
    links = owner.get(base).json()["links"]
    assert len([link for link in links if link["status"] == "active"]) == 1
    assert all(
        set(link) == {"id", "created_at", "expires_at", "status", "feedback"} for link in links
    )
    assert owner.delete("/api/review-links/" + new.json()["link"]["id"]).status_code == 204
    assert owner.get(public_path(new.json())).status_code == 404


def test_creation_rejects_changed_preview_and_foreign_or_sample_take(owner: TestClient) -> None:
    rid = run()
    other = run()
    foreign, _ = take(other)
    sample, _ = take(rid, scope="sample")
    for tid in [foreign, sample]:
        assert (
            owner.post(f"/api/runs/{rid}/review-link/preview", json=choice(tid)).status_code == 409
        )
    base = f"/api/runs/{rid}/review-link"
    preview = owner.post(base + "/preview", json=choice()).json()
    with session_scope() as db:
        node = db.scalars(select(RunNode).where(RunNode.run_id == rid)).one()
        node.artifact_hash = store().put("script", script("Changed while preview open")).hash
    assert owner.post(base, json={**choice(), "preview_key": preview["key"]}).status_code == 409
    assert owner.get(base).json()["links"] == []


def test_auth_csrf_configuration_and_expiry(
    owner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = run()
    base = f"/api/runs/{rid}/review-link"
    assert (
        owner.post(
            base + "/preview", json=choice(), headers={"Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    assert (
        owner.post(
            base + "/preview", json=choice(), headers={"Sec-Fetch-Site": "cross-site"}
        ).status_code
        == 403
    )
    prepared = owner.post(base + "/preview", json=choice()).json()
    monkeypatch.setattr(get_settings(), "review_public_origin", "")
    assert owner.post(base, json={**choice(), "preview_key": prepared["key"]}).status_code == 409
    assert owner.get(base).json()["configured"] is False
    monkeypatch.setattr(get_settings(), "review_public_origin", "https://review.example.test")
    created = share(owner, rid, choice())
    path = public_path(created)
    with session_scope() as db:
        db.get(ReviewLink, created["link"]["id"]).expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
    assert owner.get(path).status_code == 404
    assert (
        owner.get(path).json()["title"]
        == owner.get("/api/public/review-links/invalid").json()["title"]
    )
    renewed = share(owner, rid, choice())
    assert owner.get(public_path(renewed)).status_code == 200
    owner.cookies.clear()
    assert owner.get(base).status_code == 401
    assert owner.post(base + "/preview", json=choice()).status_code == 401
    assert owner.delete("/api/review-links/" + renewed["link"]["id"]).status_code == 401


def test_series_freezes_roster_order_and_missing_audio(owner: TestClient) -> None:
    seed = run()
    plan = SeriesPlan(
        title="Series",
        episodes=[
            EpisodePlan(
                id=f"episode-{i}",
                index=i,
                title=f"Episode {i}",
                target_minutes=10,
                supportable_minutes=10,
            )
            for i in [1, 2]
        ],
        budget=SeriesBudget(
            max_supportable_minutes=20, minutes_per_episode=10, verdict="ok", explanation="Enough"
        ),
    )
    with session_scope() as db:
        row = Series(
            document_id=db.get(Run, seed).document_id,
            flow_id="baseline_v0",
            flow_version="1.0",
            plan_flow_id="series_plan_v0",
            status="completed",
            plan_artifact_hash=store().put("series_plan", plan).hash,
        )
        db.add(row)
        db.flush()
        sid = row.id
    r1 = run(sid, 1)
    r2 = run(sid, 2)
    run(sid, 0)
    tid, _ = take(r1)
    payload = {
        "title": "Public series",
        "episodes": [
            {"index": 1, "title": "One", "take_id": tid},
            {"index": 2, "title": "Two", "take_id": None},
        ],
        "acknowledge_missing_audio": True,
    }
    base = f"/api/series/{sid}/review-link"
    options = owner.get(base + "/options").json()
    assert [e["index"] for e in options["episodes"]] == [1, 2]
    assert (
        owner.post(
            base + "/preview", json={**payload, "episodes": payload["episodes"][:1]}
        ).status_code
        == 409
    )
    key = owner.post(base + "/preview", json=payload).json()["key"]
    response = owner.post(base, json={**payload, "preview_key": key})
    assert response.status_code == 201, response.text
    path = public_path(response.json())
    original = owner.get(path).json()
    assert [e["title"] for e in original["episodes"]] == ["One", "Two"]
    assert original["episodes"][0]["audio"] and original["episodes"][1]["audio"] is None
    assert owner.get(path + "/episodes/0/audio").status_code == 404
    assert owner.get(path + "/episodes/999/audio").status_code == 404
    with session_scope() as db:
        plan.episodes.reverse()
        db.get(Series, sid).plan_artifact_hash = store().put("series_plan", plan).hash
        db.get(Run, r2).status = "running"
    take(r2)
    assert owner.get(path).json() == original
    assert owner.get(base + "/options").status_code == 409


def test_ready_full_take_survives_newer_failed_take_and_empty_script_blocks(
    owner: TestClient,
) -> None:
    rid = run()
    ready, _ = take(rid)
    take(rid, status="failed")
    take(rid, scope="sample")
    body = owner.get(f"/api/runs/{rid}/review-link/options").json()
    assert [t["id"] for t in body["episodes"][0]["takes"]] == [ready]
    with session_scope() as db:
        node = db.scalars(select(RunNode).where(RunNode.run_id == rid)).one()
        node.artifact_hash = store().put("script", Script(segments=[])).hash
    assert owner.post(f"/api/runs/{rid}/review-link/preview", json=choice()).status_code == 409


def test_invalid_token_rate_limit_does_not_block_valid_grant(owner: TestClient) -> None:
    created = share(owner, run(), choice())
    for _ in range(30):
        assert owner.get("/api/public/review-links/invalid").status_code == 404
    assert owner.get("/api/public/review-links/invalid").status_code == 429
    assert owner.get(public_path(created)).status_code == 200


def test_simultaneous_replacements_cannot_revoke_each_others_new_link(owner: TestClient) -> None:
    rid = run()
    original = share(owner, rid, choice())
    base = f"/api/runs/{rid}/review-link"
    prepared = owner.post(base + "/preview", json=choice()).json()
    payload = {
        **choice(),
        "preview_key": prepared["key"],
        "replace_link_id": original["link"]["id"],
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: owner.post(base, json=payload), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    created = next(response.json() for response in responses if response.status_code == 201)
    assert owner.get(public_path(created)).status_code == 200
    assert owner.get(public_path(original)).status_code == 404
    assert (
        len([link for link in owner.get(base).json()["links"] if link["status"] == "active"]) == 1
    )


def test_reader_deep_link_serves_spa_without_auth_and_with_privacy_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "index.html").write_text("<html>reader shell</html>", encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))
    with TestClient(create_app()) as visitor:
        result = visitor.get("/r/" + "a" * 43 + "?episode=2")
        assert result.status_code == 200
        assert result.text == "<html>reader shell</html>"
        assert result.headers["cache-control"] == "private, no-store"
        assert result.headers["referrer-policy"] == "no-referrer"
        assert result.headers["x-robots-tag"] == "noindex, nofollow"


def test_missing_media_preserves_readable_script_and_corrupt_snapshot_is_generic(
    owner: TestClient,
) -> None:
    rid = run()
    tid, blob = take(rid)
    created = share(owner, rid, choice(tid))
    path = public_path(created)
    media = store().blob_path(blob, ".mp3")
    saved_media = media.read_bytes()
    media.unlink()
    assert owner.get(path).status_code == 200
    assert owner.get(path + "/episodes/1/audio").status_code == 404
    with session_scope() as db:
        snapshot_hash = db.get(ReviewLink, created["link"]["id"]).snapshot_hash
    snapshot = store().path_for(snapshot_hash)
    saved_snapshot = snapshot.read_bytes()
    try:
        snapshot.write_text("invalid JSON", encoding="utf-8")
        result = owner.get(path)
        assert result.status_code == 404
        assert snapshot_hash not in result.text
        assert result.headers["cache-control"] == "private, no-store"
    finally:
        snapshot.write_bytes(saved_snapshot)
        media.write_bytes(saved_media)


def _reviewer(n: int = 1) -> dict[str, str]:
    return {"X-Reviewer-Key": f"{n:064x}"}


def test_marks_toggle_comments_and_slop_stay_on_the_frozen_line(owner: TestClient) -> None:
    rid = run()
    created = share(owner, rid, choice())
    path = public_path(created) + "/feedback/marks"
    owner.cookies.clear()
    missing = owner.put(path, json={"episode": 1, "ordinal": 0, "reaction": "dislike"})
    assert missing.status_code == 422
    saved = owner.put(
        path,
        headers=_reviewer(),
        json={
            "episode": 1,
            "ordinal": 0,
            "reaction": "dislike",
            "slop": True,
            "comment": "klingt hölzern",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["marks"] == [
        {
            "episode": 1,
            "ordinal": 0,
            "reaction": "dislike",
            "slop": True,
            "comment": "klingt hölzern",
        }
    ]
    assert (
        owner.put(
            path,
            headers=_reviewer(),
            json={"episode": 1, "ordinal": 0, "reaction": "impressed", "slop": True},
        ).status_code
        == 422
    )
    kept = owner.put(
        path,
        headers=_reviewer(),
        json={"episode": 1, "ordinal": 0, "reaction": "impressed", "slop": False, "comment": "gut"},
    )
    assert kept.json()["marks"][0]["reaction"] == "impressed"
    assert kept.json()["marks"][0]["slop"] is False
    assert kept.json()["marks"][0]["comment"] == "gut"
    cleared = owner.put(
        path, headers=_reviewer(), json={"episode": 1, "ordinal": 0, "reaction": None}
    )
    assert cleared.json()["marks"] == []
    assert (
        owner.put(
            path, headers=_reviewer(), json={"episode": 1, "ordinal": 9, "reaction": "horrible"}
        ).status_code
        == 422
    )


def test_sheet_half_stars_and_separate_browsers(
    owner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = run()
    created = share(owner, rid, choice())
    path = public_path(created) + "/feedback/sheet"
    owner.cookies.clear()
    for stars in (0, 0.3, 5.5):
        assert owner.put(path, headers=_reviewer(), json={"stars": stars}).status_code == 422
    sent = owner.put(
        path,
        headers=_reviewer(1),
        json={"label": "Lena", "stars": 3.5, "worked": "Stimme", "did_not": "Tempo"},
    )
    assert sent.status_code == 200
    assert sent.json()["stars"] == 3.5 and sent.json()["label"] == "Lena"
    assert owner.put(path, headers=_reviewer(2), json={"stars": 5}).status_code == 200
    monkeypatch.setattr("app.sharing_feedback.MAX_KEYS", 2)
    assert owner.put(path, headers=_reviewer(3), json={"stars": 1}).status_code == 409
    with session_scope() as db:
        link = db.get(ReviewLink, created["link"]["id"])
        assert link
        stored = db.scalars(
            select(ReviewFeedback).where(ReviewFeedback.review_link_id == link.id)
        ).all()
        assert len(stored) == 2
        assert {row.snapshot_hash for row in stored} == {link.snapshot_hash}
        raw = {_reviewer(1)["X-Reviewer-Key"], _reviewer(2)["X-Reviewer-Key"]}
        assert all(row.key_hash not in raw for row in stored)


def _login(owner: TestClient) -> None:
    with session_scope() as db:
        email = db.scalars(select(User.email).order_by(User.created_at.desc())).first()
    logged_in = owner.post(
        "/api/auth/login", json={"email": email, "password": "test-password"}
    )
    assert logged_in.status_code == 200


def test_replacement_keeps_marks_on_the_old_stand(owner: TestClient) -> None:
    rid = run()
    tid, _blob = take(rid)
    created = share(owner, rid, choice(tid))
    path = public_path(created)
    assert (
        owner.put(
            path + "/feedback/marks",
            headers=_reviewer(),
            json={"episode": 1, "ordinal": 0, "reaction": "horrible", "comment": "zu glatt"},
        ).status_code
        == 200
    )
    base = f"/api/runs/{rid}/review-link"
    prepared = owner.post(base + "/preview", json=choice(tid)).json()
    new = owner.post(
        base,
        json={
            **choice(tid),
            "preview_key": prepared["key"],
            "replace_link_id": created["link"]["id"],
        },
    )
    assert new.status_code == 201
    links = owner.get(base).json()["links"]
    old = next(link for link in links if link["id"] == created["link"]["id"])
    fresh = next(link for link in links if link["id"] == new.json()["link"]["id"])
    assert old["feedback"]["horrible"] == 1
    assert old["feedback"]["lines"][0]["key"] == "e1-s00"
    assert old["feedback"]["lines"][0]["comment"] == "zu glatt"
    assert fresh["feedback"]["responses"] == []
    owner.cookies.clear()
    assert (
        owner.put(
            path + "/feedback/marks",
            headers=_reviewer(),
            json={"episode": 1, "ordinal": 0, "reaction": "dislike"},
        ).status_code
        == 404
    )
    new_path = public_path(new.json())
    assert (
        owner.put(
            new_path + "/feedback/marks",
            headers=_reviewer(),
            json={"episode": 1, "ordinal": 0, "reaction": "dislike"},
        ).status_code
        == 200
    )
    _login(owner)
    assert owner.delete("/api/review-links/" + new.json()["link"]["id"]).status_code == 204
    owner.cookies.clear()
    refused = owner.put(new_path + "/feedback/sheet", headers=_reviewer(), json={"stars": 1})
    assert refused.status_code == 404
    _login(owner)
    links = owner.get(base).json()["links"]
    revoked = next(link for link in links if link["id"] == new.json()["link"]["id"])
    assert revoked["feedback"]["dislike"] == 1


def test_public_feedback_does_not_open_a_review_session(owner: TestClient) -> None:
    rid = run()
    created = share(owner, rid, choice())
    with session_scope() as db:
        before = db.scalar(select(func.count()).select_from(ReviewSession))
        status = db.get(Run, rid).status
    owner.cookies.clear()
    assert (
        owner.put(
            public_path(created) + "/feedback/marks",
            headers=_reviewer(),
            json={"episode": 1, "ordinal": 0, "reaction": "impressed"},
        ).status_code
        == 200
    )
    assert owner.get(f"/api/runs/{rid}/script").status_code == 401
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(ReviewSession)) == before
        assert db.get(Run, rid).status == status


def test_write_limit_does_not_keep_the_comment(
    owner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = run()
    created = share(owner, rid, choice())
    owner.cookies.clear()
    _write_attempts.clear()
    monkeypatch.setattr("app.sharing_feedback.WRITE_LIMIT", 1)
    path = public_path(created) + "/feedback/marks"
    body = {"episode": 1, "ordinal": 0, "reaction": "dislike", "comment": "geheim"}
    assert owner.put(path, headers=_reviewer(), json=body).status_code == 200
    refused = owner.put(path, headers=_reviewer(), json=body)
    assert refused.status_code == 429
    assert "geheim" not in refused.text
    assert "geheim" not in repr(_write_attempts)


def test_cited_page_is_frozen_without_private_ids(owner: TestClient) -> None:
    digest = uuid4().hex + uuid4().hex[:32]
    pdf = fitz.open()
    pdf.new_page()
    pdf.save(get_settings().uploads_dir / f"{digest}.pdf")
    pdf.close()
    with session_scope() as db:
        document = Document(filename="private-source.pdf", sha256=digest, title="Private title")
        db.add(document)
        db.flush()
        parsed = ParsedDocument(
            document_id=document.id,
            parse_version=1,
            language="de",
            page_count=1,
            page_sizes=[(595.0, 842.0)],
            blocks=[
                Block(
                    id="block-private",
                    ordinal=0,
                    text="Hallo Quelle",
                    page=0,
                    bboxes=[(0, (10.0, 20.0, 80.0, 40.0))],
                )
            ],
            report=IngestionReport(
                language="de",
                page_count=1,
                extractable_words=2,
                narratable_words=2,
                visual_content_ratio=0,
                text_density=1,
                structure_source="flat",
                structure_confidence="low",
                section_count=0,
            ),
        )
        document.parsed_artifact_hash = store().put("parsed", parsed).hash
        script_row = Script(
            segments=[
                Segment(
                    id="seg-private",
                    speaker="Host",
                    text="Gesprochene Zeile",
                    kind="claim",
                    beat_id="beat-private",
                    anchors=[
                        Anchor(
                            document_id=document.id,
                            parse_version=1,
                            block_id="block-private",
                            char_start=0,
                            char_end=5,
                        )
                    ],
                )
            ]
        )
        row = Run(
            document_id=document.id,
            flow_id="baseline_v0",
            flow_version="1.0",
            status="completed",
            name="Private run",
        )
        db.add(row)
        db.flush()
        db.add(
            RunNode(
                run_id=row.id,
                node_name="script",
                node_version="0",
                cache_key=uuid4().hex * 2,
                artifact_hash=store().put("script", script_row).hash,
            )
        )
        run_id = row.id
        private_id = document.id
    created = share(owner, run_id, choice())
    preview = owner.post(f"/api/runs/{run_id}/review-link/preview", json=choice()).json()
    preview_page = preview["snapshot"]["episodes"][0]["pages"][0]["url"]
    assert preview_page.startswith("/api/review-snapshots/")
    assert owner.get(preview_page).content.startswith(b"\x89PNG")
    owner.cookies.clear()
    assert owner.get(preview_page).status_code == 401
    body = owner.get(public_path(created))
    text = body.text
    assert "block-private" not in text
    assert "seg-private" not in text
    assert "beat-private" not in text
    assert private_id not in text
    segment = body.json()["episodes"][0]["segments"][0]
    assert segment["ordinal"] == 0
    assert segment["citations"][0]["rects"][0]["page"] == 0
    page = body.json()["episodes"][0]["pages"][0]
    image = owner.get(page["url"])
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    assert owner.get(page["url"].rsplit("/", 1)[0] + "/" + ("ab" * 32)).status_code == 404


def test_old_snapshot_without_ordinals_still_reads(owner: TestClient) -> None:
    rid = run()
    content = {
        "schema_version": 1,
        "title": "Old stand",
        "episodes": [
            {
                "index": 1,
                "title": "Old episode",
                "segments": [{"speaker": "Host", "text": "Old line"}],
                "audio": None,
            }
        ],
    }
    token = secrets.token_urlsafe(32)
    with session_scope() as db:
        user = db.scalars(select(User).order_by(User.created_at.desc())).first()
        assert user
        db.add(
            ReviewLink(
                target_kind="runs",
                target_id=rid,
                created_by=user.id,
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(days=30),
                token_digest=hashlib.sha256(token.encode()).hexdigest(),
                snapshot_hash=store().put_raw("review_snapshot", content).hash,
            )
        )
    owner.cookies.clear()
    body = owner.get(f"/api/public/review-links/{token}").json()
    assert body["episodes"][0]["segments"] == [
        {"ordinal": 0, "speaker": "Host", "text": "Old line", "citations": []}
    ]
    assert body["episodes"][0]["pages"] == []
