"""Display names for a run or a series.

A blank name means there is none: the console keeps the title it showed before
names existed (the document title, or the plan title of a series).
"""

from __future__ import annotations

from app.errors import problem

#: Trimmed length. Unicode is kept; this counts characters, not bytes.
NAME_MAX_LENGTH = 200


def clean_name(value: str | None) -> str | None:
    """Trim a display name. Blank means unnamed; over the limit is a 422."""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > NAME_MAX_LENGTH:
        raise problem(
            422,
            "Name too long",
            f"A name is at most {NAME_MAX_LENGTH} characters.",
        )
    return cleaned
