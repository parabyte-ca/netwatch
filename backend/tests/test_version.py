"""Guards dev-sop versioning: VERSION, pyproject, package.json, README badge and CHANGELOG agree."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_versions_in_sync():
    version = (ROOT / "VERSION").read_text().strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    pyproject = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == version
    assert json.loads((ROOT / "frontend" / "package.json").read_text())["version"] == version
    assert f"version-{version}-blue" in (ROOT / "README.md").read_text()
    assert f"## [{version}]" in (ROOT / "CHANGELOG.md").read_text()
