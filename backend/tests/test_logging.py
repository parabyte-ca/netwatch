import ast
import logging
from pathlib import Path

from netwatch.logging_setup import JsonFormatter

RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}
SRC = Path(__file__).resolve().parents[1] / "netwatch"


def test_no_log_extra_shadows_logrecord_attributes():
    offenders = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "extra" and isinstance(kw.value, ast.Dict):
                    for key in kw.value.keys:
                        if isinstance(key, ast.Constant) and key.value in RESERVED:
                            offenders.append(f"{path.name}:{node.lineno} {key.value}")
    assert offenders == []


def test_json_formatter_includes_extras():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("w",), None)
    record.target_id = 7
    out = JsonFormatter().format(record)
    assert '"msg": "hello w"' in out and '"target_id": 7' in out
