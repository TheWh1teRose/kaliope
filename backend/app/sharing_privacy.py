"""Public review responses are uncacheable; bearer paths are redacted in app access logs.

The original ASGI scope is used by uvicorn's access logger. Routing receives a
copy with the original path, while the protocol's scope gets a redacted path
and no reviewer key. Upstream Cloud Run/proxy logs need their own release-time
access/retention review.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADERS = {
    b"cache-control": b"private, no-store",
    b"referrer-policy": b"no-referrer",
    b"x-robots-tag": b"noindex, nofollow",
}


class SharingPrivacyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        public = path.startswith(("/r/", "/api/public/review-links/"))
        sharing = public or "/review-link" in path
        if scope["type"] != "http" or not sharing:
            await self.app(scope, receive, send)
            return
        routed = dict(scope)
        if public:
            prefix = "/r/" if path.startswith("/r/") else "/api/public/review-links/"
            scope["path"] = prefix + "[redacted]"
            scope["raw_path"] = scope["path"].encode()
            scope["query_string"] = b""
            if "headers" in scope:
                routed["headers"] = list(scope["headers"])
                scope["headers"] = [
                    (name, value)
                    for name, value in scope["headers"]
                    if name.lower() != b"x-reviewer-key"
                ]

        async def private_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = dict(message)
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() not in HEADERS
                ]
                message["headers"] = headers + list(HEADERS.items())
            await send(message)

        await self.app(routed, receive, private_send)
