"""Shared fixtures: a fresh in-memory tracer per test, shut down afterwards."""

from __future__ import annotations

import pytest

from agent_tracer import setup_tracing


@pytest.fixture
def tracing():
    handle = setup_tracing("memory")
    yield handle
    handle.shutdown()


@pytest.fixture
def tracing_with_content():
    handle = setup_tracing("memory", capture_content=True)
    yield handle
    handle.shutdown()


def by_name(spans, name):
    return [s for s in spans if s.name == name]
