"""Shared test setup.

Tests must not depend on whatever happens to be in ``backend/.env``. Before this
existed, adding a real ``SERPAPI_KEY`` to .env made the suite fail *and* fire live
SerpApi requests (burning free-tier quota) on every run, because SerpApiProvider
became "available" and pre-empted the demo provider the tests assert against.

So: neutralize provider credentials for the whole suite by default. A test that
wants a provider configured opts in explicitly via the ``configure_provider``
helper below.
"""
from __future__ import annotations

import pytest

from app.config import get_settings

# Credentials that decide which provider `available()` picks. Cleared per-test.
_PROVIDER_KEYS = (
    "serpapi_key",
    "movieglu_api_key",
    "movieglu_client",
    "movieglu_authorization",
    "google_places_api_key",
)


@pytest.fixture(autouse=True)
def isolate_provider_config(monkeypatch):
    """Blank out provider keys so tests never hit a live API by accident."""
    settings = get_settings()
    for key in _PROVIDER_KEYS:
        monkeypatch.setattr(settings, key, "", raising=False)
    yield settings


@pytest.fixture
def configure_provider(monkeypatch):
    """Opt back in to a provider credential for a single test.

    Usage: ``configure_provider(serpapi_key="test-key")``. The value is fake —
    pair it with a stubbed ``_request``/``fetch`` so no network call happens.
    """
    settings = get_settings()

    def _set(**values):
        for key, value in values.items():
            monkeypatch.setattr(settings, key, value, raising=False)
        return settings

    return _set
