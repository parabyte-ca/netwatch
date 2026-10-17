"""Container HEALTHCHECK: exits 0 when /health reports ok. Uses only the standard library."""

import json
import sys
import urllib.request

from .config import get_settings


def main() -> int:
    s = get_settings()
    host = "127.0.0.1" if s.bind in ("0.0.0.0", "::", "") else s.bind
    if ":" in host:
        host = f"[{host}]"
    try:
        with urllib.request.urlopen(f"http://{host}:{s.port}/health", timeout=4) as resp:
            return 0 if json.load(resp).get("status") == "ok" else 1
    except Exception as exc:
        print(f"health check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
