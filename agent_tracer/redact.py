"""Redaction of personal data and secrets.

Exists because traces get stored, shared and sent to other tools. Anything that might hold user
content (prompts, completions, tool arguments, error messages) passes through `redact()` first.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_KEYS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    # 32+ char hex/base64-like run containing at least one digit (so long words survive)
    re.compile(r"(?<![A-Za-z0-9+/_-])(?=[A-Za-z0-9+/_-]*\d)[A-Za-z0-9+/_-]{32,}={0,2}"),
]
_PHONE = re.compile(r"(?<![\w+])\+?\d(?:[ -]?\d){9,}(?!\w)")


def redact(text: str) -> str:
    """Replace emails, phone numbers and API keys/tokens with placeholders."""
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    for pattern in _KEYS:
        text = pattern.sub("[REDACTED_KEY]", text)
    return _PHONE.sub("[REDACTED_PHONE]", text)
