from __future__ import annotations

import pytest

from app.settings import AppSettings


@pytest.fixture(autouse=True)
def isolate_settings_from_developer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must not read the developer's real .env (credentials, CA paths, backends)."""
    monkeypatch.setitem(AppSettings.model_config, "env_file", None)
