"""In-process pub/sub for run progress (§7.6).

The whole application is one container with one process, so SSE fan-out is a
dictionary of queues rather than a broker. Subscribers that fall behind lose
events rather than blocking the worker — progress is a convenience, and the run
record is the source of truth.
"""

from __future__ import annotations

import contextlib
import json
import queue
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

#: Events buffered per subscriber before the slowest ones start dropping.
QUEUE_SIZE = 256
#: Replay buffer per run, so a client that connects mid-run sees what it missed.
HISTORY_SIZE = 200


@dataclass
class Event:
    run_id: str
    type: str
    data: dict[str, Any]
    at: str

    def to_sse(self) -> str:
        payload = json.dumps({"type": self.type, "at": self.at, **self.data})
        return f"event: {self.type}\ndata: {payload}\n\n"


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


bus = EventBus()
