from __future__ import annotations

import uvicorn

from .config import Settings
from .web import create_app


settings = Settings.from_env()
app = create_app(settings)


def run() -> None:
    uvicorn.run(
        app,
        host=settings.web_host,
        port=settings.web_port,
        access_log=False,
        server_header=False,
    )


if __name__ == "__main__":
    run()
