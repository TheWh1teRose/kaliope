"""In-process pub/sub for run progress (§7.6).

The whole application is one container with one process, so SSE fan-out is a
dictionary of queues rather than a broker. Subscribers that fall behind lose
events rather than blocking the worker — progress is a convenience, and the run
record is the source of truth.

Every open stream is a request in flight, and Cloud Run serves only a few of
those at once. ``follow`` therefore waits on the event loop rather than in a
worker thread, closes itself after ``STREAM_LIFETIME_SECONDS`` so a stream the
client left without the server noticing gives its slot back, and closes early
once nothing is driving the job any more. Events carry ids, so a client that
reopens a stream asks for what came after the last one it saw.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import queue
import threading
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

#: Events buffered per subscriber before the slowest ones start dropping.
QUEUE_SIZE = 256
#: Replay buffer per run, so a client that connects mid-run sees what it missed.
HISTORY_SIZE = 200
#: Seconds between heartbeats on a quiet stream; also how often it checks the job.
HEARTBEAT_SECONDS = 15.0
#: Longest a stream stays open before it asks the client to reopen it.
STREAM_LIFETIME_SECONDS = 120.0
#: How often an open stream looks at its queue.
POLL_SECONDS = 0.2

#: Sent before a stream closes at its lifetime: the client reopens it at once.
RENEW = 'event: stream.renew\ndata: {"type": "stream.renew"}\n\n'
#: Sent when nothing drives the job any more: the client stops following it.
IDLE = 'event: stream.idle\ndata: {"type": "stream.idle"}\n\n'

_ids = itertools.count(1)


@dataclass
class Event:
    run_id: str
    type: str
    data: dict[str, Any]
    at: str
    #: Increases across every channel, so ids stay ordered across a ``clear``.
    id: int = 0

    def to_sse(self) -> str:
        payload = json.dumps({"type": self.type, "at": self.at, **self.data})
        return f"id: {self.id}\nevent: {self.type}\ndata: {payload}\n\n"


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[queue.Queue[Event]]] = defaultdict(list)
        self._history: dict[str, list[Event]] = defaultdict(list)

    def publish(self, run_id: str, event_type: str, data: dict[str, Any] | None = None) -> None:
        event = Event(
            run_id=run_id,
            type=event_type,
            data=data or {},
            at=datetime.now(UTC).isoformat(),
        )
        with self._lock:
            event.id = next(_ids)
            history = self._history[run_id]
            history.append(event)
            if len(history) > HISTORY_SIZE:
                del history[: len(history) - HISTORY_SIZE]
            subscribers = list(self._subscribers[run_id])
        for subscriber in subscribers:
            # A subscriber that has fallen behind loses events rather than
            # blocking the worker; the run record is the source of truth.
            with contextlib.suppress(queue.Full):
                subscriber.put_nowait(event)

    def subscribe(self, run_id: str) -> tuple[queue.Queue[Event], list[Event]]:
        subscriber: queue.Queue[Event] = queue.Queue(maxsize=QUEUE_SIZE)
        with self._lock:
            self._subscribers[run_id].append(subscriber)
            replay = list(self._history[run_id])
        return subscriber, replay

    def unsubscribe(self, run_id: str, subscriber: queue.Queue[Event]) -> None:
        with self._lock:
            if subscriber in self._subscribers[run_id]:
                self._subscribers[run_id].remove(subscriber)
            if not self._subscribers[run_id]:
                del self._subscribers[run_id]

    def clear(self, run_id: str) -> None:
        with self._lock:
            self._history.pop(run_id, None)

    def subscribers(self, run_id: str) -> int:
        """How many streams are open on ``run_id``."""
        with self._lock:
            return len(self._subscribers.get(run_id, ()))

    async def follow(
        self,
        run_id: str,
        *,
        terminal: frozenset[str],
        closing: str | None = None,
        after: int | None = None,
        live: Callable[[], bool] | None = None,
    ) -> AsyncIterator[str]:
        """The server-sent events of ``run_id``, from the replay on.

        ``closing`` ends the stream after the replay, for a job already over.
        ``after`` skips the events a reopened stream has already seen. ``live``
        says whether something still drives the job; once it says no, the
        stream sends ``stream.idle`` and closes instead of beating forever.
        """
        subscriber, replay = self.subscribe(run_id)
        try:
            yield ": connected\n\n"
            for event in replay:
                if after is not None and event.id <= after:
                    continue
                yield event.to_sse()
                if event.type in terminal:
                    return
            if closing is not None:
                yield closing
                return
            if live is not None and not live():
                yield IDLE
                return
            loop = asyncio.get_running_loop()
            deadline = loop.time() + STREAM_LIFETIME_SECONDS
            beat = loop.time() + HEARTBEAT_SECONDS
            while True:
                try:
                    event = subscriber.get_nowait()
                except queue.Empty:
                    now = loop.time()
                    if now >= deadline:
                        yield RENEW
                        return
                    if now >= beat:
                        if live is not None and not live():
                            yield IDLE
                            return
                        yield ": heartbeat\n\n"
                        beat = now + HEARTBEAT_SECONDS
                    await asyncio.sleep(POLL_SECONDS)
                    continue
                yield event.to_sse()
                if event.type in terminal:
                    return
        finally:
            self.unsubscribe(run_id, subscriber)


def seen_until(after: int | None, last_event_id: str | None) -> int | None:
    """The last event id a reopened stream saw: the ``after`` query or the browser's header."""
    if after is not None:
        return after
    if last_event_id and last_event_id.strip().isdigit():
        return int(last_event_id.strip())
    return None


bus = EventBus()
