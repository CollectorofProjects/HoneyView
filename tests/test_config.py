import pytest

from app.config import Settings


def test_production_mode_fails_without_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("HONEYVIEW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HONEYVIEW_REQUIRE_EXPLICIT_SECRETS", "true")
    monkeypatch.delenv("HONEYVIEW_DASHBOARD_PASSWORD", raising=False)
    monkeypatch.delenv("HONEYVIEW_DASHBOARD_PASSWORD_FILE", raising=False)
    with pytest.raises(ValueError, match="DASHBOARD_PASSWORD"):
        Settings.from_env()


def test_production_secrets_can_come_from_files(monkeypatch, tmp_path):
    password_file = tmp_path / "password"
    fingerprint_file = tmp_path / "fingerprint"
    password_file.write_text("a-secure-dashboard-password-123")
    fingerprint_file.write_text("an-independent-fingerprint-key-456")
    monkeypatch.setenv("HONEYVIEW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HONEYVIEW_REQUIRE_EXPLICIT_SECRETS", "true")
    monkeypatch.setenv("HONEYVIEW_DASHBOARD_PASSWORD_FILE", str(password_file))
    monkeypatch.setenv("HONEYVIEW_FINGERPRINT_KEY_FILE", str(fingerprint_file))
    settings = Settings.from_env()
    assert settings.dashboard_password.startswith("a-secure")
    assert settings.fingerprint_key.startswith("an-independent")
