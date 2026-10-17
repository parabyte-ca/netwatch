"""CLI: reset an admin password from the host, signing out every session.

    docker exec -it netwatch python -m netwatch.reset_password [username]

Prompts for the new password (or reads one line from stdin with --stdin). With no admin
account yet, it creates one.
"""

import argparse
import getpass
import sys

from .auth import AuthStore, password_problem
from .config import get_settings
from .db import make_engine, run_migrations


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="netwatch.reset_password")
    parser.add_argument("username", nargs="?", default="admin")
    parser.add_argument("--stdin", action="store_true", help="read the password from stdin")
    args = parser.parse_args(argv)

    if args.stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("New password: ")
        if getpass.getpass("Repeat: ") != password:
            print("passwords do not match", file=sys.stderr)
            return 1
    if problem := password_problem(password):
        print(problem, file=sys.stderr)
        return 1

    engine = make_engine(get_settings().db_path)
    run_migrations(engine)
    store = AuthStore(engine)
    if store.user_count() == 0:
        store.create_user(args.username, password)
        print(f"created admin account {args.username!r}")
    elif store.set_password_by_name(args.username, password):
        print(f"password reset for {args.username!r}; all sessions signed out")
    else:
        print(f"no such user {args.username!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
