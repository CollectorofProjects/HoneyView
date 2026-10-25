from __future__ import annotations

import asyncio
import os
import time
from contextlib import suppress

import asyncssh

from .config import Settings
from .events import EventBus
from .shell import DecoyShell
from .store import Store


def _peer(connection) -> tuple[str, int]:
    value = connection.get_extra_info("peername") or ("unknown", 0)
    return str(value[0]), int(value[1])


class HoneypotSSHServer(asyncssh.SSHServer):
    def __init__(self, store: Store, bus: EventBus, owner: "SSHHoneypot") -> None:
        self.store = store
        self.bus = bus
        self.owner = owner
        self.connection = None

    def connection_made(self, conn) -> None:
        self.connection = conn
        self.owner.register_connection(conn)

    def connection_lost(self, exc) -> None:
        if self.connection is not None:
            self.owner.unregister_connection(self.connection)

    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        source_ip, source_port = _peer(self.connection)
        accepted = bool(username) and len(username) <= 128 and len(password) <= 512
        attempt_id = self.store.record_auth(
            source_ip=source_ip,
            source_port=source_port,
            username=username,
            auth_method="password",
            secret=password,
            accepted=accepted,
        )
        setattr(self.connection, "_honey_auth_method", "password")
        self.bus.publish(
            {
                "type": "auth_attempt",
                "id": attempt_id,
                "source_ip": source_ip,
                "username": username[:128],
                "auth_method": "password",
                "credential_length": len(password),
            }
        )
        return accepted

    def public_key_auth_supported(self) -> bool:
        return True

    def validate_public_key(self, username: str, key) -> bool:
        source_ip, source_port = _peer(self.connection)
        fingerprint = key.get_fingerprint("sha256")
        accepted = bool(username) and len(username) <= 128
        attempt_id = self.store.record_auth(
            source_ip=source_ip,
            source_port=source_port,
            username=username,
            auth_method="public_key",
            secret=fingerprint,
            accepted=accepted,
        )
        setattr(self.connection, "_honey_auth_method", "public_key")
        self.bus.publish(
            {
                "type": "auth_attempt",
                "id": attempt_id,
                "source_ip": source_ip,
                "username": username[:128],
                "auth_method": "public_key",
            }
        )
        return accepted

    def connection_requested(self, *args) -> bool:
        return False

    def server_requested(self, *args) -> bool:
        return False


class SSHHoneypot:
    def __init__(self, settings: Settings, store: Store, bus: EventBus) -> None:
        self.settings = settings
        self.store = store
        self.bus = bus
        self.listener = None
        self._connections: set = set()
        self._control_lock = asyncio.Lock()
        self._session_slots = asyncio.Semaphore(settings.max_concurrent_sessions)

    @property
    def is_running(self) -> bool:
        return self.listener is not None

    @property
    def active_connections(self) -> int:
        return len(self._connections)

    def register_connection(self, connection) -> None:
        self._connections.add(connection)

    def unregister_connection(self, connection) -> None:
        self._connections.discard(connection)

    def ensure_host_key(self) -> None:
        path = self.settings.ssh_host_key_path
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        key = asyncssh.generate_private_key("ssh-ed25519")
        path.write_bytes(key.export_private_key())
        os.chmod(path, 0o600)

    async def start(self) -> bool:
        async with self._control_lock:
            if self.listener is not None:
                return False
            self.ensure_host_key()
            self.listener = await asyncssh.create_server(
                lambda: HoneypotSSHServer(self.store, self.bus, self),
                self.settings.ssh_host,
                self.settings.ssh_port,
                server_host_keys=[str(self.settings.ssh_host_key_path)],
                process_factory=self._handle_process,
                encoding="utf-8",
                line_editor=False,
                x11_forwarding=False,
                agent_forwarding=False,
                server_version=self.settings.ssh_server_version,
                login_timeout=30,
            )
            self.bus.publish({"type": "sensor_status", "running": True})
            return True

    async def stop(self) -> bool:
        async with self._control_lock:
            listener = self.listener
            connections = list(self._connections)
            if listener is None and not connections:
                return False

            self.listener = None
            if listener is not None:
                listener.close()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(listener.wait_closed(), timeout=5)

            for connection in connections:
                with suppress(Exception):
                    connection.close()
            if connections:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        asyncio.gather(
                            *(connection.wait_closed() for connection in connections),
                            return_exceptions=True,
                        ),
                        timeout=5,
                    )
            self._connections.clear()
            self.bus.publish({"type": "sensor_status", "running": False})
            return True

    async def close(self) -> None:
        await self.stop()

    async def _handle_process(self, process: asyncssh.SSHServerProcess) -> None:
        try:
            await asyncio.wait_for(self._session_slots.acquire(), timeout=0.05)
        except asyncio.TimeoutError:
            process.stderr.write("System is temporarily unavailable.\n")
            process.exit(1)
            return
        connection = process.get_extra_info("connection")
        source_ip, source_port = _peer(connection)
        username = str(process.get_extra_info("username") or "root")
        auth_method = str(getattr(connection, "_honey_auth_method", "unknown"))
        client_version = str(connection.get_extra_info("client_version") or "")
        terminal_type = str(process.get_extra_info("term_type") or "")
        session = self.store.create_session(
            source_ip=source_ip,
            source_port=source_port,
            username=username,
            auth_method=auth_method,
            client_version=client_version,
            terminal_type=terminal_type,
        )
        session_id = session["id"]
        self.bus.publish({"type": "session_opened", "session": session})
        reason = "client_exit"
        try:
            if process.command:
                await self._handle_exec(process, session_id, username, process.command)
            else:
                await self._handle_shell(process, session_id, username)
        except asyncio.TimeoutError:
            reason = "idle_timeout"
            with suppress(Exception):
                process.stdout.write("\r\nConnection timed out.\r\n")
        except (asyncssh.BreakReceived, asyncssh.TerminalSizeChanged):
            reason = "terminal_event"
        except Exception as exc:
            reason = f"transport_error:{type(exc).__name__}"
        finally:
            event = self.store.close_session(session_id, reason)
            self.bus.publish({"type": "session_closed", "event": event})
            with suppress(Exception):
                process.exit(0)
            self._session_slots.release()

    async def _handle_exec(self, process, session_id: str, username: str, command: str) -> None:
        shell = DecoyShell(username)
        start = time.monotonic()
        command = command[: self.settings.max_command_length]
        output, _ = shell.execute(command)
        event, _ = self.store.add_command(
            session_id,
            command=command,
            typed_ms=0,
            gap_ms=None,
            since_start_ms=round((time.monotonic() - start) * 1000),
        )
        self.bus.publish({"type": "command", "event": event})
        process.stdout.write(output)

    async def _handle_shell(self, process, session_id: str, username: str) -> None:
        shell = DecoyShell(username)
        started = time.monotonic()
        previous_command_at: float | None = None
        process.stdout.write(
            "Welcome to Ubuntu 22.04.4 LTS (GNU/Linux 5.15.0-119-generic x86_64)\r\n\r\n"
        )
        process.stdout.write(shell.prompt)
        buffer: list[str] = []
        first_key_at: float | None = None

        while time.monotonic() - started < self.settings.session_max_seconds:
            char = await asyncio.wait_for(
                process.stdin.read(1), timeout=self.settings.session_idle_seconds
            )
            if not char:
                break
            now = time.monotonic()
            if first_key_at is None:
                first_key_at = now
            if char in {"\r", "\n"}:
                process.stdout.write("\r\n")
                command = "".join(buffer)[: self.settings.max_command_length]
                buffer.clear()
                if command.strip():
                    typed_ms = round((now - (first_key_at or now)) * 1000)
                    gap_ms = (
                        round((first_key_at - previous_command_at) * 1000)
                        if previous_command_at is not None and first_key_at is not None
                        else None
                    )
                    event, _ = self.store.add_command(
                        session_id,
                        command=command,
                        typed_ms=typed_ms,
                        gap_ms=gap_ms,
                        since_start_ms=round((now - started) * 1000),
                    )
                    self.bus.publish({"type": "command", "event": event})
                    output, should_exit = shell.execute(command)
                    process.stdout.write(output.replace("\n", "\r\n"))
                    previous_command_at = now
                    if should_exit:
                        return
                first_key_at = None
                process.stdout.write(shell.prompt)
            elif char in {"\x7f", "\b"}:
                if buffer:
                    buffer.pop()
                    process.stdout.write("\b \b")
            elif char == "\x03":
                buffer.clear()
                first_key_at = None
                process.stdout.write("^C\r\n" + shell.prompt)
            elif char == "\x04":
                if not buffer:
                    return
            elif char.isprintable() and len(buffer) < self.settings.max_command_length:
                buffer.append(char)
                process.stdout.write(char)
