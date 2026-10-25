from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .classifier import Classification, classify


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, path: Path, fingerprint_key: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        self._fingerprint_key = fingerprint_key.encode()
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    last_seen TEXT NOT NULL,
                    source_ip TEXT NOT NULL,
                    source_port INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    auth_method TEXT NOT NULL,
                    client_version TEXT NOT NULL DEFAULT '',
                    terminal_type TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    command_count INTEGER NOT NULL DEFAULT 0,
                    automation_score INTEGER NOT NULL DEFAULT 0,
                    actor_class TEXT NOT NULL DEFAULT 'unknown',
                    classification_reasons TEXT NOT NULL DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS auth_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    source_ip TEXT NOT NULL,
                    source_port INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    auth_method TEXT NOT NULL,
                    credential_fingerprint TEXT,
                    credential_length INTEGER,
                    accepted INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_sessions_ip ON sessions(source_ip);
                CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_events_time ON events(occurred_at DESC);
                CREATE INDEX IF NOT EXISTS idx_auth_time ON auth_attempts(occurred_at DESC);
                """
            )
            self._ensure_column("sessions", "client_version", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column("sessions", "terminal_type", "TEXT NOT NULL DEFAULT ''")

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"] for row in self._connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in columns:
            self._connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    def fingerprint(self, secret: str) -> str:
        return hmac.new(self._fingerprint_key, secret.encode(), hashlib.sha256).hexdigest()[:20]

    def record_auth(
        self,
        *,
        source_ip: str,
        source_port: int,
        username: str,
        auth_method: str,
        secret: str | None,
        accepted: bool = True,
    ) -> int:
        fingerprint = self.fingerprint(secret) if secret is not None else None
        secret_length = len(secret) if secret is not None else None
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO auth_attempts (
                    occurred_at, source_ip, source_port, username, auth_method,
                    credential_fingerprint, credential_length, accepted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now(), source_ip, source_port, username[:128], auth_method,
                    fingerprint, secret_length, int(accepted),
                ),
            )
            return int(cursor.lastrowid)

    def create_session(
        self,
        *,
        source_ip: str,
        source_port: int,
        username: str,
        auth_method: str,
        client_version: str = "",
        terminal_type: str = "",
    ) -> dict:
        session_id = str(uuid.uuid4())
        now = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO sessions (
                    id, started_at, last_seen, source_ip, source_port, username, auth_method,
                    client_version, terminal_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id, now, now, source_ip, source_port, username[:128], auth_method,
                    client_version[:256], terminal_type[:64],
                ),
            )
            self._insert_event(session_id, "session_opened", {"username": username[:128]}, now)
        return self.get_session(session_id)

    def _insert_event(self, session_id: str | None, event_type: str, payload: dict, now: str) -> int:
        cursor = self._connection.execute(
            "INSERT INTO events (session_id, occurred_at, event_type, payload) VALUES (?, ?, ?, ?)",
            (session_id, now, event_type, json.dumps(payload, separators=(",", ":"))),
        )
        return int(cursor.lastrowid)

    def add_event(self, session_id: str | None, event_type: str, payload: dict) -> dict:
        now = utc_now()
        with self._lock, self._connection:
            event_id = self._insert_event(session_id, event_type, payload, now)
            if session_id:
                self._connection.execute(
                    "UPDATE sessions SET last_seen = ? WHERE id = ?", (now, session_id)
                )
        return {
            "id": event_id,
            "session_id": session_id,
            "occurred_at": now,
            "event_type": event_type,
            "payload": payload,
        }

    def add_command(
        self,
        session_id: str,
        *,
        command: str,
        typed_ms: int,
        gap_ms: int | None,
        since_start_ms: int,
    ) -> tuple[dict, Classification]:
        payload = {
            "command": command,
            "typed_ms": typed_ms,
            "gap_ms": gap_ms,
            "since_start_ms": since_start_ms,
        }
        now = utc_now()
        with self._lock, self._connection:
            event_id = self._insert_event(session_id, "command", payload, now)
            self._connection.execute(
                """
                UPDATE sessions
                SET command_count = command_count + 1, last_seen = ?
                WHERE id = ?
                """,
                (now, session_id),
            )
            command_rows = self._connection.execute(
                "SELECT payload FROM events WHERE session_id = ? AND event_type = 'command' ORDER BY id",
                (session_id,),
            ).fetchall()
            classification = classify([json.loads(row["payload"]) for row in command_rows])
            self._connection.execute(
                """
                UPDATE sessions
                SET automation_score = ?, actor_class = ?, classification_reasons = ?
                WHERE id = ?
                """,
                (
                    classification.score,
                    classification.label,
                    json.dumps(classification.reasons),
                    session_id,
                ),
            )
        return (
            {
                "id": event_id,
                "session_id": session_id,
                "occurred_at": now,
                "event_type": "command",
                "payload": payload,
                "classification": {
                    "score": classification.score,
                    "label": classification.label,
                    "reasons": classification.reasons,
                },
            },
            classification,
        )

    def close_session(self, session_id: str, reason: str) -> dict:
        now = utc_now()
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE sessions SET status = 'closed', ended_at = ?, last_seen = ?
                WHERE id = ? AND status = 'active'
                """,
                (now, now, session_id),
            )
            event_id = self._insert_event(session_id, "session_closed", {"reason": reason}, now)
        return {
            "id": event_id,
            "session_id": session_id,
            "occurred_at": now,
            "event_type": "session_closed",
            "payload": {"reason": reason},
        }

    @staticmethod
    def _session(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["classification_reasons"] = json.loads(item["classification_reasons"])
        return item

    def get_session(self, session_id: str) -> dict:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return self._session(row)

    def list_sessions(self, limit: int = 100) -> list[dict]:
        limit = max(1, min(limit, 500))
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._session(row) for row in rows]

    def list_events(self, limit: int = 200, session_id: str | None = None) -> list[dict]:
        limit = max(1, min(limit, 1000))
        with self._lock:
            if session_id:
                rows = self._connection.execute(
                    "SELECT * FROM events WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        events = []
        for row in reversed(rows):
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            events.append(item)
        return events

    def summary(self) -> dict:
        with self._lock:
            session = self._connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(status = 'active') AS active,
                       COALESCE(SUM(command_count), 0) AS commands,
                       SUM(actor_class = 'likely_automated') AS automated,
                       COUNT(DISTINCT source_ip) AS distinct_ips
                FROM sessions
                """
            ).fetchone()
            auth = self._connection.execute(
                "SELECT COUNT(*) AS total FROM auth_attempts"
            ).fetchone()
            hourly = self._connection.execute(
                """
                SELECT substr(started_at, 1, 13) || ':00:00Z' AS hour, COUNT(*) AS count
                FROM sessions
                WHERE started_at >= ?
                GROUP BY substr(started_at, 1, 13)
                ORDER BY hour
                """,
                ((datetime.now(UTC) - timedelta(hours=24)).isoformat(),),
            ).fetchall()
        return {
            "sessions": int(session["total"] or 0),
            "active": int(session["active"] or 0),
            "commands": int(session["commands"] or 0),
            "likely_automated": int(session["automated"] or 0),
            "distinct_ips": int(session["distinct_ips"] or 0),
            "auth_attempts": int(auth["total"] or 0),
            "hourly": [dict(row) for row in hourly],
        }

    def analytics(self, hours: int = 24) -> dict:
        hours = max(1, min(hours, 24 * 90))
        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        with self._lock:
            top_sources = self._connection.execute(
                """
                SELECT source_ip, COUNT(*) AS sessions, SUM(command_count) AS commands,
                       MAX(automation_score) AS max_score, MAX(last_seen) AS last_seen
                FROM sessions WHERE started_at >= ?
                GROUP BY source_ip ORDER BY sessions DESC, commands DESC LIMIT 12
                """,
                (cutoff,),
            ).fetchall()
            top_users = self._connection.execute(
                """
                SELECT username, COUNT(*) AS attempts
                FROM auth_attempts WHERE occurred_at >= ?
                GROUP BY username ORDER BY attempts DESC LIMIT 10
                """,
                (cutoff,),
            ).fetchall()
            auth_methods = self._connection.execute(
                """
                SELECT auth_method, COUNT(*) AS count
                FROM auth_attempts WHERE occurred_at >= ?
                GROUP BY auth_method ORDER BY count DESC
                """,
                (cutoff,),
            ).fetchall()
            actor_classes = self._connection.execute(
                """
                SELECT actor_class, COUNT(*) AS count
                FROM sessions WHERE started_at >= ?
                GROUP BY actor_class ORDER BY count DESC
                """,
                (cutoff,),
            ).fetchall()
            heatmap = self._connection.execute(
                """
                SELECT CAST(strftime('%w', started_at) AS INTEGER) AS weekday,
                       CAST(strftime('%H', started_at) AS INTEGER) AS hour,
                       COUNT(*) AS count
                FROM sessions WHERE started_at >= ?
                GROUP BY weekday, hour
                """,
                (cutoff,),
            ).fetchall()
            command_rows = self._connection.execute(
                """
                SELECT payload FROM events
                WHERE event_type = 'command' AND occurred_at >= ?
                ORDER BY id DESC LIMIT 10000
                """,
                (cutoff,),
            ).fetchall()
            duration_row = self._connection.execute(
                """
                SELECT AVG((julianday(COALESCE(ended_at, last_seen)) - julianday(started_at)) * 86400.0)
                       AS average_seconds
                FROM sessions WHERE started_at >= ?
                """,
                (cutoff,),
            ).fetchone()

        command_counts: dict[str, int] = {}
        indicators = {"downloaders": 0, "persistence": 0, "recon": 0, "execution": 0}
        for row in command_rows:
            command = str(json.loads(row["payload"]).get("command") or "").strip()
            if not command:
                continue
            display = command[:160]
            command_counts[display] = command_counts.get(display, 0) + 1
            lowered = command.lower()
            if "curl " in lowered or "wget " in lowered:
                indicators["downloaders"] += 1
            if any(value in lowered for value in ("crontab", "systemctl enable", "authorized_keys")):
                indicators["persistence"] += 1
            if any(value in lowered for value in ("uname", "whoami", " id", "cat /etc", "ps ")):
                indicators["recon"] += 1
            if any(value in lowered for value in ("chmod +x", "./", "sh -c", "bash -c", "| sh")):
                indicators["execution"] += 1

        top_commands = sorted(
            ({"command": command, "count": count} for command, count in command_counts.items()),
            key=lambda item: (-item["count"], item["command"]),
        )[:12]
        return {
            "hours": hours,
            "top_sources": [dict(row) for row in top_sources],
            "top_users": [dict(row) for row in top_users],
            "auth_methods": [dict(row) for row in auth_methods],
            "actor_classes": [dict(row) for row in actor_classes],
            "heatmap": [dict(row) for row in heatmap],
            "top_commands": top_commands,
            "indicators": indicators,
            "average_session_seconds": round(float(duration_row["average_seconds"] or 0), 1),
        }

    def export_sessions(self, hours: int = 24 * 30, limit: int = 10000) -> list[dict]:
        hours = max(1, min(hours, 24 * 90))
        limit = max(1, min(limit, 10000))
        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM sessions WHERE started_at >= ? ORDER BY started_at DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        return [self._session(row) for row in rows]

    def purge(self, retention_days: int) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM sessions WHERE started_at < ?", (cutoff,)
            )
            self._connection.execute(
                "DELETE FROM auth_attempts WHERE occurred_at < ?", (cutoff,)
            )
        return int(cursor.rowcount)

    def close(self) -> None:
        with self._lock:
            self._connection.close()
