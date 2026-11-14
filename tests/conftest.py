"""Keep automated tests independent of the operator's production OAuth secrets."""
import pytest
from app.dashboard import google_auth


@pytest.fixture(autouse=True)
def isolated_google_configuration(tmp_path, monkeypatch):
    # OAuth-specific tests explicitly install their own fixture configuration.
    monkeypatch.setattr(google_auth, 'ENV_PATH', tmp_path / 'unconfigured-google.env')
