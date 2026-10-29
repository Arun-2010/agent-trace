"""Unit tests for redact() (test 12)."""

from __future__ import annotations

import pytest

from agent_tracer import redact


@pytest.mark.parametrize(
    "text, expected",
    [
        ("mail me at jane.doe+work@mail.example.co.uk", "mail me at [REDACTED_EMAIL]"),
        ("call +1 415-555-0123 now", "call [REDACTED_PHONE] now"),
        ("call 9876543210", "call [REDACTED_PHONE]"),
        ("phone: +91 98765 43210.", "phone: [REDACTED_PHONE]."),
        ("key=sk-proj-AbC123xyz789", "key=[REDACTED_KEY]"),
        ("Authorization: Bearer eyJhbGciOi.abc.def", "Authorization: [REDACTED_KEY]"),
        ("hash 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08", "hash [REDACTED_KEY]"),
        ("token QWxhZGRpbjpvcGVuIHNlc2FtZQ12345678==", "token [REDACTED_KEY]"),
    ],
)
def test_redacts(text, expected):
    assert redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Convert 100 USD to INR and tell me the weather in Hyderabad",
        "Order 12345 shipped on 2024-05-01 for $19.99",
        "pneumonoultramicroscopicsilicovolcanoconiosis is a long word",
        "",
    ],
)
def test_text_without_secrets_unchanged(text):
    assert redact(text) == text
