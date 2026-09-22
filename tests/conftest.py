"""Shared pytest fixtures.

The Jev quality gate is opt-out, so /api/generate would call the live Decisions
API whenever a developer happens to have OPENROUTER_API_KEY exported. Tests must
run offline with no key, so the key is stripped from the environment for every
test by default. A test that wants the gate active sets it explicitly with
monkeypatch.setenv and stubs app.call_jev_decisions.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(autouse=True)
def no_live_jev_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
