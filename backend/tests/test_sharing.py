"""Public bearer access, frozen script/audio pairing and atomic owner sharing."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

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
    ReviewLink,
    ReviewSession,
    Run,
    RunNode,
    Series,
    User,
)
from app.pipeline.framework.artifacts import ArtifactStore
from app.schemas.audio import AudioMix
from app.schemas.pipeline import EpisodePlan, Script, Segment, SeriesBudget, SeriesPlan
from app.security import hash_password


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
    assert set(episode) == {"index", "title", "segments", "audio"}
    assert episode["segments"] == [{"speaker": "Host", "text": "Original recorded text"}]
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
    assert all(set(link) == {"id", "created_at", "expires_at", "status"} for link in links)
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
