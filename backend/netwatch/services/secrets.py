"""Encryption for stored credentials (e.g. the Deco owner password).

The key lives in its own file next to the database (``data/.secret_key``, mode 0600), never in
the database itself, so a database backup downloaded from the Admin page cannot reveal a
stored password. If the key file is lost, stored secrets simply have to be entered again.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

KEY_NAME = ".secret_key"


class SecretBox:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / KEY_NAME
        self._fernet: Fernet | None = None

    def _key(self) -> Fernet:
        if self._fernet is None:
            if not self.path.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(Fernet.generate_key())
            self._fernet = Fernet(self.path.read_bytes().strip())
        return self._fernet

    def encrypt(self, plaintext: str) -> str:
        return self._key().encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str | None:
        """The plaintext, or None if the key changed or the token is damaged."""
        try:
            return self._key().decrypt(token.encode()).decode()
        except (InvalidToken, ValueError):
            return None
