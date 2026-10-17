"""NetWatch: self-hosted network diagnostics and root-cause analysis."""

import os
from pathlib import Path


def _read_version() -> str:
    """Resolve the running version: APP_VERSION (set at image build) wins, then VERSION file."""
    env = os.environ.get("APP_VERSION", "").strip()
    if env:
        return env.lstrip("v")
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "VERSION"
        if candidate.is_file():
            return candidate.read_text().strip()
    return "0.0.0"


__version__ = _read_version()
