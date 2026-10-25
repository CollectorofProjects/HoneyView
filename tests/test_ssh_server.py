import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.events import EventBus
from app.ssh_server import HoneypotSSHServer, SSHHoneypot


class FakeListener:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


class FakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


def test_sensor_start_stop_is_idempotent_and_closes_connections(monkeypatch, tmp_path):
    async def scenario():
        listener = FakeListener()
        captured = {}

        async def fake_create_server(factory, *args, **kwargs):
            captured["factory"] = factory
            return listener

        monkeypatch.setattr("app.ssh_server.asyncssh.create_server", fake_create_server)
        settings = SimpleNamespace(
            max_concurrent_sessions=4,
            ssh_host_key_path=Path(tmp_path / "host-key"),
            ssh_host="127.0.0.1",
            ssh_port=2222,
            ssh_server_version="OpenSSH_test",
        )
        bus = EventBus()
        queue = bus.register()
        sensor = SSHHoneypot(settings, SimpleNamespace(), bus)
        monkeypatch.setattr(sensor, "ensure_host_key", lambda: None)

        assert await sensor.start() is True
        assert await sensor.start() is False
        assert sensor.is_running is True
        assert (await queue.get()) == {"type": "sensor_status", "running": True}

        connection = FakeConnection()
        server = captured["factory"]()
        assert isinstance(server, HoneypotSSHServer)
        server.connection_made(connection)
        assert sensor.active_connections == 1

        assert await sensor.stop() is True
        assert await sensor.stop() is False
        assert listener.closed is True
        assert connection.closed is True
        assert sensor.is_running is False
        assert sensor.active_connections == 0
        assert (await queue.get()) == {"type": "sensor_status", "running": False}

    asyncio.run(scenario())
