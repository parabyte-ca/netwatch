from __future__ import annotations

from fastapi import Request

from ..config import Settings
from ..services.scheduler import Monitor
from ..services.storage import Store


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_monitor(request: Request) -> Monitor:
    return request.app.state.monitor


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings
