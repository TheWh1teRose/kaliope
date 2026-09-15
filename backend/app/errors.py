"""RFC 9457 problem+json errors (§11)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

CONTENT_TYPE = "application/problem+json"


class ProblemException(HTTPException):
    def __init__(
        self,
        status_code: int,
        title: str,
        detail: str | None = None,
        *,
        type_: str = "about:blank",
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail or title)
        self.title = title
        self.type_ = type_
        self.extra = extra or {}


def problem(
    status_code: int,
    title: str,
    detail: str | None = None,
    *,
    type_: str = "about:blank",
    **extra: Any,
) -> ProblemException:
    return ProblemException(status_code, title, detail, type_=type_, extra=extra)


def problem_response(request: Request, exc: ProblemException) -> JSONResponse:
    body: dict[str, Any] = {
        "type": exc.type_,
        "title": exc.title,
        "status": exc.status_code,
        "detail": exc.detail,
        "instance": str(request.url.path),
    }
    body.update(exc.extra)
    return JSONResponse(status_code=exc.status_code, content=body, media_type=CONTENT_TYPE)


def http_exception_response(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc, ProblemException):
        return problem_response(request, exc)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "type": "about:blank",
            "title": _default_title(exc.status_code),
            "status": exc.status_code,
            "detail": str(exc.detail),
            "instance": str(request.url.path),
        },
        media_type=CONTENT_TYPE,
        headers=getattr(exc, "headers", None),
    )


def validation_exception_response(request: Request, exc: Exception) -> JSONResponse:
    errors: Any = getattr(exc, "errors", lambda: [])()
    return JSONResponse(
        status_code=422,
        content={
            "type": "about:blank",
            "title": "Validation error",
            "status": 422,
            "detail": "The request body did not match the expected schema.",
            "instance": str(request.url.path),
            "errors": _jsonable(errors),
        },
        media_type=CONTENT_TYPE,
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


_TITLES = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    409: "Conflict",
    413: "Payload too large",
    415: "Unsupported media type",
    422: "Validation error",
    500: "Internal server error",
}


def _default_title(status_code: int) -> str:
    return _TITLES.get(status_code, "Error")
