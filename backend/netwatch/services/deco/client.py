"""Client for the TP-Link Deco local web API (the one the Deco app and web UI use).

Ported from the MIT-licensed Home Assistant integration by amosyuen
(https://github.com/amosyuen/ha-tplink-deco, custom_components/tplink_deco/api.py),
adapted to httpx and to NetWatch's read-only needs (device and client lists).

The API is undocumented. Requests are AES-CBC encrypted with a random numeric key and IV and
signed with the router's RSA key; the session is a ``stok`` path token plus a ``sysauth``
cookie. Some firmwares instead wrap the API in the encrypted session of the local web UI
("outer session"), detected by HTTP 401 on the key endpoint. Only the Deco *owner* account
(username "admin") can sign in, and an owner sign-in elsewhere ends this session.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import math
import re
import secrets
from typing import Any
from urllib.parse import quote_plus

import httpx
from cryptography.hazmat.primitives import padding as sympad
from cryptography.hazmat.primitives.asymmetric import padding as asympad
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

log = logging.getLogger(__name__)

AES_DIGITS = 16
MIN_AES = 10 ** (AES_DIGITS - 1)
MAX_AES = 10**AES_DIGITS - 1
PKCS1_HEADER = 11
_ERROR_DECODING = re.compile(r"^<Error Decoding (.*)>$")


class DecoError(Exception):
    """Any failure talking to the Deco; the message is safe to show in the UI."""


class DecoLoginError(DecoError):
    pass


class _Reauth(Exception):
    """Session expired or was taken over: sign in again and retry once."""


# ── crypto helpers ────────────────────────────────────────────────────────────


def _byte_len(n: int) -> int:
    return (int(math.log2(n)) + 8) >> 3


def rsa_encrypt(n: int, e: int, plaintext: bytes) -> str:
    """PKCS#1 v1.5 in fixed-size blocks, hex-concatenated (TP-Link's scheme)."""
    key = RSAPublicNumbers(e, n).public_key()
    per_block = _byte_len(n) - PKCS1_HEADER
    out = ""
    for i in range(0, len(plaintext), per_block):
        out += key.encrypt(plaintext[i : i + per_block], asympad.PKCS1v15()).hex()
    return out


def aes_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    padder = sympad.PKCS7(algorithms.AES.block_size).padder()
    data = padder.update(plaintext) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return enc.update(data) + enc.finalize()


def aes_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    data = dec.update(ciphertext) + dec.finalize()
    return data[: -data[-1]] if data else data


def decode_name(name: Any) -> Any:
    """Deco names are base64; fall back to the raw value."""
    if not isinstance(name, str) or not name:
        return name
    with contextlib.suppress(ValueError, UnicodeDecodeError):
        name = base64.b64decode(name, validate=True).decode("utf-8")
    m = _ERROR_DECODING.match(name)
    return m.group(1) if m else name


def _numeric_secret() -> str:
    return str(secrets.randbelow(MAX_AES - MIN_AES) + MIN_AES)


# ── client ────────────────────────────────────────────────────────────────────


class DecoClient:
    def __init__(
        self,
        host: str,
        password: str,
        *,
        username: str = "admin",
        timeout_s: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        host = host.strip().rstrip("/")
        self.host = host if host.startswith(("http://", "https://")) else f"http://{host}"
        self.username = username
        self.password = password
        self._http = httpx.AsyncClient(
            timeout=timeout_s,
            verify=False,  # noqa: S501 - Decos use a self-signed certificate on https
            follow_redirects=True,
            transport=transport,
        )
        self._lock = asyncio.Lock()
        self._aes_key = _numeric_secret()
        self._aes_iv = _numeric_secret()
        self._pw_rsa: tuple[int, int] | None = None
        self._sign_rsa: tuple[int, int] | None = None
        self._seq: int | None = None
        self._stok: str | None = None
        self._cookie: str | None = None
        # legacy "outer session" (some firmwares)
        self._outer = False
        self._outer_id: str | None = None
        self._outer_rsa: tuple[int, int] | None = None
        self._outer_seq = 0
        self._outer_key = ""
        self._outer_iv = ""

    async def aclose(self) -> None:
        await self._http.aclose()

    # ── public API ───────────────────────────────────────────────────────────

    async def list_decos(self) -> list[dict]:
        data = await self._call("admin/device", "device_list", {"operation": "read"})
        decos = data.get("result", {}).get("device_list") or []
        for d in decos:
            for k in ("custom_nickname", "nickname"):
                if d.get(k):
                    d[k] = decode_name(d[k])
        return decos

    async def list_clients(self, deco_mac: str = "default") -> list[dict]:
        payload = {"operation": "read", "params": {"device_mac": deco_mac}}
        data = await self._call("admin/client", "client_list", payload)
        clients = data.get("result", {}).get("client_list") or []
        for c in clients:
            c["name"] = decode_name(c.get("name"))
        return clients

    # ── request plumbing ─────────────────────────────────────────────────────

    async def _call(self, path: str, form: str, payload: dict) -> dict:
        async with self._lock:
            for attempt in (1, 2):
                try:
                    if self._stok is None:
                        await self._login()
                    return await self._authed_post(path, form, payload)
                except _Reauth as exc:
                    self._clear()
                    if attempt == 2:
                        raise DecoError(f"session rejected after signing in again: {exc}") from exc
                except httpx.TimeoutException as exc:
                    if attempt == 2:
                        raise DecoError("the Deco did not answer in time") from exc
                except httpx.HTTPError as exc:
                    raise DecoError(f"cannot reach the Deco: {exc}") from exc
        raise DecoError("unreachable")  # pragma: no cover

    def _clear(self) -> None:
        self._seq = self._stok = self._cookie = None
        self._outer_id = None

    def _headers(self, form_encoded: bool) -> dict:
        h = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
            if form_encoded
            else "application/json"
        }
        if self._cookie:
            h["Cookie"] = self._cookie
        return h

    async def _post(self, url: str, params: dict, body: str, *, form_encoded: bool = False):
        r = await self._http.post(
            url, params=params, content=body, headers=self._headers(form_encoded)
        )
        if (
            r.history
            and r.url.scheme == "https"
            and self.host.startswith("http://")
            and r.url.host == httpx.URL(self.host).host
        ):
            self.host = "https://" + self.host[len("http://") :]
            log.warning("Deco redirected to HTTPS; using %s", self.host)
        for value in r.headers.get_list("set-cookie"):
            m = re.search(r"(sysauth=[A-Za-z0-9]+)", value)
            if m:
                self._cookie = m.group(1)
        return r

    async def _authed_post(self, path: str, form: str, payload: dict) -> dict:
        if self._outer:
            return await self._outer_call(path, form, payload)
        body = self._encode(payload)
        url = f"{self.host}/cgi-bin/luci/;stok={self._stok}/{path}"
        r = await self._post(url, {"form": form}, body)
        if r.status_code in (401, 403):
            raise _Reauth(f"HTTP {r.status_code}")
        r.raise_for_status()
        return self._decrypt_response(r)

    def _decrypt_response(self, r: httpx.Response) -> dict:
        try:
            outer = r.json()
        except ValueError as exc:
            raise DecoError("unexpected (non-JSON) answer from the Deco") from exc
        code = outer.get("error_code")
        if code not in (None, 0, ""):
            raise _Reauth(f"error_code {code}")
        enc = outer.get("data")
        if isinstance(enc, dict):
            return enc
        if not enc:
            raise _Reauth("empty data")
        try:
            plain = aes_decrypt(
                self._aes_key.encode(), self._aes_iv.encode(), base64.b64decode(enc)
            )
            data = json.loads(plain)
        except (ValueError, IndexError) as exc:
            raise _Reauth("undecodable data") from exc
        err = data.get("error_code") or data.get("errorcode")
        if err:
            raise DecoError(f"Deco error {err}")
        return data

    def _encode(self, payload: dict) -> str:
        data = base64.b64encode(
            aes_encrypt(
                self._aes_key.encode(),
                self._aes_iv.encode(),
                json.dumps(payload, separators=(",", ":")).encode(),
            )
        ).decode()
        if self._seq is None or self._sign_rsa is None:
            raise _Reauth("no signing key")
        auth_hash = hashlib.md5(f"{self.username}{self.password}".encode()).hexdigest()  # noqa: S324 - protocol
        sign_text = f"k={self._aes_key}&i={self._aes_iv}&h={auth_hash}&s={self._seq + len(data)}"
        sign = rsa_encrypt(self._sign_rsa[0], self._sign_rsa[1], sign_text.encode())
        return f"sign={sign}&data={quote_plus(data)}"

    # ── LuCI login ───────────────────────────────────────────────────────────

    async def _login(self) -> None:
        if self._outer:
            await self._outer_login()
            return
        login_url = f"{self.host}/cgi-bin/luci/;stok=/login"
        read = json.dumps({"operation": "read"})
        r = await self._post(login_url, {"form": "keys"}, read)
        if r.status_code == 401:
            log.info("Deco key endpoint returned 401; using the web-UI session protocol")
            self._outer = True
            await self._outer_login()
            return
        r.raise_for_status()
        try:
            n, e = r.json()["result"]["password"]
            self._pw_rsa = (int(n, 16), int(e, 16))
            r = await self._post(login_url, {"form": "auth"}, read)
            r.raise_for_status()
            auth = r.json()["result"]
            self._sign_rsa = (int(auth["key"][0], 16), int(auth["key"][1], 16))
            self._seq = int(auth["seq"])
        except (KeyError, ValueError, TypeError, IndexError) as exc:
            raise DecoError("this does not look like a Deco (unexpected login keys)") from exc
        enc_pw = rsa_encrypt(self._pw_rsa[0], self._pw_rsa[1], self.password.encode())
        body = self._encode({"params": {"password": enc_pw}, "operation": "login"})
        r = await self._post(login_url, {"form": "login"}, body)
        if r.status_code == 403:
            raise DecoLoginError(
                "sign-in refused (403): another owner session is active, e.g. the Deco app"
            )
        r.raise_for_status()
        try:
            data = json.loads(
                aes_decrypt(
                    self._aes_key.encode(),
                    self._aes_iv.encode(),
                    base64.b64decode(r.json()["data"]),
                )
            )
        except (KeyError, ValueError, IndexError) as exc:
            raise DecoError("unexpected sign-in answer from the Deco") from exc
        code = data.get("error_code")
        if code == -5002:
            left = (data.get("result") or {}).get("attemptsAllowed", "?")
            raise DecoLoginError(f"wrong owner password ({left} attempts left before lockout)")
        if code not in (0, None) or not (data.get("result") or {}).get("stok"):
            raise DecoLoginError(f"sign-in failed (error {code})")
        self._stok = data["result"]["stok"]
        if not self._cookie:
            raise DecoError("the Deco did not set a session cookie")
        log.info("signed in to the Deco")

    # ── legacy outer (web-UI) session ────────────────────────────────────────

    @staticmethod
    def _security_encode(left: str, right: str, alphabet: str) -> str:
        out = []
        for i in range(max(len(left), len(right))):
            lc = ord(left[i]) if i < len(left) else 187
            rc = ord(right[i]) if i < len(right) else 187
            out.append(alphabet[(lc ^ rc) % len(alphabet)])
        return "".join(out)

    async def _outer_post(self, params: dict, data: str = "") -> httpx.Response:
        return await self._http.post(
            f"{self.host}/",
            params=params,
            content=data,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )

    @staticmethod
    def _lines(text: str) -> list[str]:
        parts = text.rstrip("\r\n").split("\r\n")
        return parts if len(parts) > 1 else text.rstrip("\r\n").splitlines()

    async def _outer_login(self) -> None:
        r = await self._outer_post({"code": 7, "asyn": 1})
        challenge = self._lines(r.text)
        if len(challenge) < 5:
            raise DecoError("unexpected web-UI sign-in challenge")
        r = await self._outer_post({"code": 16, "asyn": 0}, "enable")
        if not r.text.startswith("00000"):
            raise DecoError("could not enable web-UI session encryption")
        r = await self._outer_post({"code": 16, "asyn": 0}, "get")
        key = self._lines(r.text)
        if len(key) < 4 or key[0] != "00000":
            raise DecoError("unexpected web-UI session key")
        self._outer_rsa = (int(key[2], 16), int(key[1], 16))  # (n, e)
        self._outer_seq = int(key[3])
        self._outer_key, self._outer_iv = _numeric_secret(), _numeric_secret()
        pw_md5 = hashlib.md5(self.password.encode()).hexdigest()  # noqa: S324 - protocol
        outer_id = self._security_encode(challenge[3], pw_md5, challenge[4])
        n, e = self._outer_rsa
        r = await self._outer_post(
            {"code": 7, "asyn": 0, "id": outer_id}, rsa_encrypt(n, e, self.password.encode())
        )
        if r.status_code != 200 or not r.text.startswith("00000"):
            raise DecoLoginError("wrong owner password (web-UI sign-in refused)")
        aes = rsa_encrypt(n, e, f"k={self._outer_key}&i={self._outer_iv}".encode())
        r = await self._outer_post({"code": 16, "asyn": 0, "id": outer_id}, f"set {aes}")
        if not r.text.startswith("00000"):
            raise DecoError("could not install the web-UI session key")
        self._outer_id = outer_id
        self._stok = ""
        log.info("signed in to the Deco (web-UI session)")

    async def _outer_call(self, path: str, form: str, payload: dict) -> dict:
        if self._outer_id is None or self._outer_rsa is None:
            raise _Reauth("no web-UI session")
        body = json.dumps(payload, separators=(",", ":"))
        enc = base64.b64encode(
            aes_encrypt(self._outer_key.encode(), self._outer_iv.encode(), body.encode())
        ).decode()
        sign_text = f"k={self._outer_key}&i={self._outer_iv}&s={self._outer_seq + len(enc)}"
        sign = rsa_encrypt(self._outer_rsa[0], self._outer_rsa[1], sign_text.encode())
        r = await self._http.post(
            f"{self.host}/{path}",
            params={"form": form, "id": self._outer_id},
            content=f"sign={sign}&data={enc}",
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        )
        if r.status_code in (401, 403) or not r.text:
            raise _Reauth(f"HTTP {r.status_code}")
        r.raise_for_status()
        try:
            data = json.loads(
                aes_decrypt(
                    self._outer_key.encode(), self._outer_iv.encode(), base64.b64decode(r.text)
                )
            )
        except (ValueError, IndexError) as exc:
            raise _Reauth("undecodable web-UI answer") from exc
        err = data.get("error_code") or data.get("errorcode")
        if err:
            raise DecoError(f"Deco error {err}")
        return data
