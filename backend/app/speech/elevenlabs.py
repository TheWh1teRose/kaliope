"""ElevenLabs Text to Dialogue over plain HTTP.

Request and response shapes follow the API reference read on 2026-10-03
(``/v1/text-to-dialogue/with-timestamps`` and ``/v2/voices``). The timestamped
variant is used because its ``voice_segments`` say when each input is heard,
which is what lets the player follow the script line by line.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from app.speech.base import DialogueRequest, SpeechError, SpeechResult, Voice, VoiceSegment

API_URL = "https://api.elevenlabs.io"


class ElevenLabsProvider:
    name = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = API_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 180.0,
    ) -> None:
        self._api_key = api_key.strip()
        self._client = httpx.Client(
            base_url=base_url,
            transport=transport,
            timeout=timeout,
            headers={"xi-api-key": self._api_key},
        )

    def available(self) -> bool:
        return bool(self._api_key)

    def dialogue(self, request: DialogueRequest) -> SpeechResult:
        body: dict[str, Any] = {
            "inputs": [item.model_dump() for item in request.inputs],
            "model_id": request.model_id,
        }
        if request.language_code:
            body["language_code"] = request.language_code
        if request.stability is not None:
            body["settings"] = {"stability": request.stability}
        if request.seed is not None:
            body["seed"] = request.seed
        if request.previous_request_ids:
            body["previous_request_ids"] = request.previous_request_ids[-3:]

        response = self._send(
            "POST",
            "/v1/text-to-dialogue/with-timestamps",
            params={"output_format": request.output_format},
            json=body,
        )
        data = response.json()
        try:
            audio = base64.b64decode(data["audio_base64"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SpeechError("ElevenLabs returned no audio") from exc
        segments = [
            VoiceSegment(
                input_index=int(item["dialogue_input_index"]),
                start_s=float(item["start_time_seconds"]),
                end_s=float(item["end_time_seconds"]),
            )
            for item in data.get("voice_segments") or []
            if isinstance(item, dict)
        ]
        cost = response.headers.get("character-cost")
        return SpeechResult(
            audio=audio,
            request_id=response.headers.get("request-id"),
            character_cost=int(cost) if cost and cost.isdigit() else None,
            segments=segments,
        )

    def voices(self) -> list[Voice]:
        """The account's voices. Default voices are left out: they expire on 2026-12-31."""
        response = self._send("GET", "/v2/voices", params={"page_size": 100})
        voices = []
        for item in response.json().get("voices") or []:
            if not isinstance(item, dict) or item.get("category") == "premade":
                continue
            raw_labels = item.get("labels")
            labels: dict[str, Any] = raw_labels if isinstance(raw_labels, dict) else {}
            voices.append(
                Voice(
                    voice_id=str(item.get("voice_id")),
                    name=str(item.get("name") or item.get("voice_id")),
                    category=item.get("category"),
                    description=item.get("description"),
                    language=labels.get("language"),
                    preview_url=item.get("preview_url"),
                )
            )
        return voices

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise SpeechError("ElevenLabs did not answer in time", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise SpeechError(f"ElevenLabs could not be reached: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            raise _error(response)
        return response


def _error(response: httpx.Response) -> SpeechError:
    status = response.status_code
    code, message = _detail(response)
    if status == 401:
        text = "ElevenLabs refused the API key (ELEVENLABS_API_KEY)"
    elif status == 402:
        text = "ElevenLabs reports too few credits for this request"
    elif status == 429:
        text = "ElevenLabs is at its rate or concurrency limit"
    else:
        text = f"ElevenLabs answered {status}"
    if message:
        text = f"{text}: {message}"
    return SpeechError(text, status=status, code=code, retryable=status == 429 or status >= 500)


def _detail(response: httpx.Response) -> tuple[str | None, str | None]:
    try:
        body = response.json()
    except ValueError:
        return None, response.text[:200] or None
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return detail.get("code") or detail.get("status"), detail.get("message")
    if isinstance(detail, list) and detail and isinstance(detail[0], dict):
        return None, str(detail[0].get("msg") or detail[0])
    if isinstance(detail, str):
        return None, detail
    return None, None
