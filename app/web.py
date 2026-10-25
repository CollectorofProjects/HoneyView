from __future__ import annotations

import asyncio
import base64
import csv
import hmac
import io
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .config import Settings
from .events import EventBus
from .ssh_server import SSHHoneypot
from .store import Store


def sensor_control_request_allowed(action_header: str, fetch_site: str) -> bool:
    return hmac.compare_digest(action_header, "sensor-control") and fetch_site in {
        "", "none", "same-origin"
    }


def create_app(settings: Settings) -> FastAPI:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(settings.database_path, settings.fingerprint_key)
    bus = EventBus()
    ssh = SSHHoneypot(settings, store, bus)
    static_dir = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await ssh.start()

        async def retention_loop() -> None:
            while True:
                await asyncio.sleep(6 * 60 * 60)
                store.purge(settings.retention_days)

        retention_task = asyncio.create_task(retention_loop())
        try:
            yield
        finally:
            retention_task.cancel()
            await ssh.close()
            store.close()

    app = FastAPI(
        title="HoneyView",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.bus = bus
    app.state.ssh = ssh

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def require_dashboard_auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        expected_user = settings.dashboard_user.encode()
        expected_password = settings.dashboard_password.encode()
        valid = False
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:], validate=True)
                username, password = decoded.split(b":", 1)
                valid = hmac.compare_digest(username, expected_user) and hmac.compare_digest(
                    password, expected_password
                )
            except (ValueError, UnicodeError):
                valid = False
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Dashboard authentication required",
                headers={"WWW-Authenticate": 'Basic realm="HoneyView", charset="UTF-8"'},
            )

    secured = Depends(require_dashboard_auth)

    def require_sensor_control(request: Request) -> None:
        require_dashboard_auth(request)
        action_header = request.headers.get("x-honeyview-action", "")
        fetch_site = request.headers.get("sec-fetch-site", "")
        if not sensor_control_request_allowed(action_header, fetch_site):
            raise HTTPException(status_code=403, detail="Cross-site sensor control denied")

    sensor_control = Depends(require_sensor_control)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/", dependencies=[secured], include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/assets/{filename}", dependencies=[secured], include_in_schema=False)
    async def asset(filename: str) -> FileResponse:
        if filename not in {"app.js", "styles.css"}:
            raise HTTPException(status_code=404)
        return FileResponse(static_dir / filename)

    @app.get("/api/summary", dependencies=[secured])
    async def summary() -> dict:
        return store.summary()

    def sensor_status() -> dict:
        return {
            "running": ssh.is_running,
            "active_connections": ssh.active_connections,
            "port": settings.ssh_port,
        }

    @app.get("/api/sensor/status", dependencies=[secured])
    async def get_sensor_status() -> dict:
        return sensor_status()

    @app.post("/api/sensor/start", dependencies=[sensor_control])
    async def start_sensor() -> dict:
        try:
            changed = await ssh.start()
        except OSError as exc:
            raise HTTPException(
                status_code=409,
                detail="Sensor could not start; verify that the SSH port is available",
            ) from exc
        return {**sensor_status(), "changed": changed}

    @app.post("/api/sensor/stop", dependencies=[sensor_control])
    async def stop_sensor() -> dict:
        changed = await ssh.stop()
        return {**sensor_status(), "changed": changed}

    @app.get("/api/sessions", dependencies=[secured])
    async def sessions(limit: int = Query(100, ge=1, le=500)) -> dict:
        return {"sessions": store.list_sessions(limit)}

    @app.get("/api/sessions/{session_id}", dependencies=[secured])
    async def session_detail(session_id: str) -> dict:
        try:
            session = store.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        return {"session": session, "events": store.list_events(1000, session_id)}

    @app.get("/api/events", dependencies=[secured])
    async def events(limit: int = Query(200, ge=1, le=1000)) -> dict:
        return {"events": store.list_events(limit)}

    @app.get("/api/analytics", dependencies=[secured])
    async def analytics(hours: int = Query(24, ge=1, le=2160)) -> dict:
        return store.analytics(hours)

    @app.get("/api/export/sessions.json", dependencies=[secured])
    async def export_json(hours: int = Query(720, ge=1, le=2160)) -> Response:
        content = json.dumps(
            {"exported_hours": hours, "sessions": store.export_sessions(hours)},
            indent=2,
        )
        return Response(
            content,
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="honeyview-sessions.json"'},
        )

    @app.get("/api/export/sessions.csv", dependencies=[secured])
    async def export_csv(hours: int = Query(720, ge=1, le=2160)) -> Response:
        output = io.StringIO()
        fields = [
            "id", "started_at", "ended_at", "source_ip", "source_port", "username",
            "auth_method", "client_version", "terminal_type", "command_count",
            "automation_score", "actor_class", "status",
        ]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in store.export_sessions(hours):
            safe = {
                key: (
                    "'" + value
                    if isinstance(value, str) and value and value[:1] in "=+-@"
                    else value
                )
                for key, value in row.items()
            }
            writer.writerow(safe)
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="honeyview-sessions.csv"'},
        )

    @app.get("/api/stream", dependencies=[secured])
    async def stream(request: Request) -> StreamingResponse:
        async def generator():
            queue = bus.register()
            try:
                yield ": connected\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                        yield f"event: activity\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                bus.unregister(queue)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return JSONResponse(
            {"error": exc.detail}, status_code=exc.status_code, headers=exc.headers
        )

    return app
