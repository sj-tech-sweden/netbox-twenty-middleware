from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _reset_webhook_settings():
    """Reset the module-level _settings singleton in the webhooks module between tests."""
    import app.api.v1.webhooks as wh_mod

    wh_mod._settings = None
    yield
    wh_mod._settings = None


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove env vars that could interfere with Settings instantiation in tests."""
    for key in (
        "NETBOX_URL",
        "NETBOX_TOKEN",
        "TWENTY_URL",
        "TWENTY_API_KEY",
        "VALKEY_HOST",
        "VALKEY_PORT",
        "PUBLIC_BASE_URL",
        "NETBOX_WEBHOOK_SECRET",
        "TWENTY_WEBHOOK_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
