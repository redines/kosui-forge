from __future__ import annotations

import re
from collections.abc import Iterable

_URL_CREDENTIALS = re.compile(r"(https?://)[^/@\s]+@", re.IGNORECASE)
_BEARER = re.compile(r"(?i)(authorization\s*:\s*(?:bearer|token)\s+)[^\s,;]+")
_TOKEN_ASSIGNMENT = re.compile(
    r"(?i)\b(token|password|passwd|secret|api[_-]?key)\s*[=:]\s*[^\s,;]+"
)


def redact(value: object, secrets: Iterable[str] = ()) -> str:
    """Return a diagnostic string with common credential forms removed."""
    text = str(value)
    for secret in sorted(
        (secret for secret in secrets if secret), key=len, reverse=True
    ):
        text = text.replace(secret, "<redacted>")
    text = _URL_CREDENTIALS.sub(r"\1<redacted>@", text)
    text = _BEARER.sub(r"\1<redacted>", text)
    text = _TOKEN_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    return text
