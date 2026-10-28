"""Never let desktop integration tests touch an installed user's Data folder."""
import pytest


@pytest.fixture(autouse=True)
def isolated_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv('WHISPERDESK_DATA', str(tmp_path))
