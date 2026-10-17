"""CLI: ``python -m netwatch.backup [dest]`` writes a consistent DB snapshot (used by update.sh)."""

import os
import sys
import time
from pathlib import Path

from . import __version__
from .config import get_settings
from .services.storage import backup_database


def main(argv: list[str]) -> int:
    os.umask(0o027)
    s = get_settings()
    if not s.db_path.exists():
        print(f"no database at {s.db_path}", file=sys.stderr)
        return 1
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = Path(argv[0]) if argv else s.backup_dir / f"netwatch-{stamp}-v{__version__}.db"
    print(backup_database(s.db_path, dest))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
