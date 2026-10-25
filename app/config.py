from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _secret(name: str) -> str:
    file_path = os.getenv(f"{name}_FILE")
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    return os.getenv(name, "")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    ssh_host_key_path: Path
    ssh_host: str
    ssh_port: int
    web_host: str
    web_port: int
    dashboard_user: str
    dashboard_password: str
    fingerprint_key: str
    retention_days: int
    session_idle_seconds: int
    session_max_seconds: int
    max_command_length: int
    max_concurrent_sessions: int
    ssh_server_version: str

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("HONEYVIEW_DATA_DIR", "./data")).resolve()
        production_secrets = _bool("HONEYVIEW_REQUIRE_EXPLICIT_SECRETS")
        dashboard_password = _secret("HONEYVIEW_DASHBOARD_PASSWORD")
        if not dashboard_password:
            if production_secrets:
                raise ValueError("HONEYVIEW_DASHBOARD_PASSWORD or its _FILE variant is required")
            dashboard_password = secrets.token_urlsafe(24)
            print(
                "WARNING: HONEYVIEW_DASHBOARD_PASSWORD is unset. "
                f"Temporary dashboard password: {dashboard_password}",
                flush=True,
            )
        fingerprint_key = _secret("HONEYVIEW_FINGERPRINT_KEY")
        if not fingerprint_key:
            if production_secrets:
                raise ValueError("HONEYVIEW_FINGERPRINT_KEY or its _FILE variant is required")
            fingerprint_key = dashboard_password
        if production_secrets and len(dashboard_password) < 24:
            raise ValueError("HONEYVIEW_DASHBOARD_PASSWORD must be at least 24 characters")
        if production_secrets and fingerprint_key == dashboard_password:
            raise ValueError("Dashboard password and fingerprint key must be different")
        return cls(
            data_dir=data_dir,
            database_path=data_dir / "honeyview.sqlite3",
            ssh_host=os.getenv("HONEYVIEW_SSH_HOST", "0.0.0.0"),
            ssh_port=_int("HONEYVIEW_SSH_PORT", 2222),
            web_host=os.getenv("HONEYVIEW_WEB_HOST", "127.0.0.1"),
            web_port=_int("HONEYVIEW_WEB_PORT", 8080),
            dashboard_user=os.getenv("HONEYVIEW_DASHBOARD_USER", "analyst"),
            dashboard_password=dashboard_password,
            fingerprint_key=fingerprint_key,
            retention_days=_int("HONEYVIEW_RETENTION_DAYS", 30),
            session_idle_seconds=_int("HONEYVIEW_SESSION_IDLE_SECONDS", 180),
            session_max_seconds=_int("HONEYVIEW_SESSION_MAX_SECONDS", 1800),
            max_command_length=_int("HONEYVIEW_MAX_COMMAND_LENGTH", 4096),
            max_concurrent_sessions=_int("HONEYVIEW_MAX_CONCURRENT_SESSIONS", 64),
            ssh_server_version=os.getenv(
                "HONEYVIEW_SSH_SERVER_VERSION", "OpenSSH_8.9p1 Ubuntu-3ubuntu0.10"
            ),
            ssh_host_key_path=data_dir / "ssh_host_ed25519_key",
        )
