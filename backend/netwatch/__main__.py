"""Entrypoint: ``python -m netwatch`` starts the API server with settings from the environment."""

import os

import uvicorn

from .config import get_settings
from .logging_setup import configure_logging


def main() -> None:
    os.umask(0o027)  # database, WAL and backups are not world-readable
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    uvicorn.run(
        "netwatch.main:app",
        host=settings.bind,
        port=settings.port,
        log_config=None,
        access_log=settings.access_log,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
