"""Read-only client for the Bell Home Hub 4000 (Sagemcom F@st 5689) web API.

Protocol reimplemented from the MIT-licensed python-sagemcom-api (Mick Vleeshouwer,
https://github.com/iMicknl/python-sagemcom-api) and checked against sagemcom_fast_exporter
and lua-sagemcom-openwrt (both MIT). JSON-RPC over ``POST /cgi/json-req`` (form field
``req``); every request is signed with ``H(H(user:nonce:H(password)):id:cnonce:JSON:path)``
where H is MD5 or SHA-512 depending on the firmware (detected from the web UI's settings).
NetWatch only ever reads (``getValue``); it never changes anything on the Hub.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import math
import re
import secrets
import urllib.parse
from typing import Any

import httpx

log = logging.getLogger(__name__)

ENDPOINT = "/cgi/json-req"
OK = ("XMO_REQUEST_NO_ERR", "Ok", "XMO_NO_ERR")
SECRET_KEY = re.compile(r"pass|key|secret|pin|psk|token|credential|wpa|wep", re.I)


class HubError(Exception):
    """Any failure talking to the Hub; the message is safe to show."""


class HubLoginError(HubError):
    pass


class _Reauth(Exception):
    pass


def optical_dbm(raw: Any, *, tx: bool = False) -> float | None:
    """Optical level in dBm from the Hub's raw value. Firmwares differ: TR-181 thousandths of a
    dBm (-19234; a transmit level is positive, e.g. 2100 = +2.1 dBm), plain dBm (-19.2), or
    linear power in 0.1 µW units (120 = -19.2 dBm)."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v == 0 or v <= -100_000:  # "no signal" / "policy" sentinels
        return None
    if v < -100:
        dbm = v / 1000
    elif v < 0:
        dbm = v
    elif tx and 0 < v <= 10:
        dbm = v  # plain dBm (lasers send about +0.5 to +5 dBm)
    elif tx and 100 < v <= 10_000:
        dbm = v / 1000  # TR-181 thousandths: +0.1 .. +10 dBm
    elif v > 100:
        dbm = 10 * math.log10(v / 10_000)
    else:
        return None
    return round(dbm, 2) if -45 <= dbm <= 10 else None


def redact(value: Any) -> Any:
    """Hide credentials (Wi-Fi keys, PPP and account passwords) in a raw tree."""
    if isinstance(value, dict):
        return {
            k: "••••"
            if SECRET_KEY.search(str(k)) and isinstance(v, str | int) and v != ""
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class HubClient:
    def __init__(
        self,
        host: str,
        password: str,
        *,
        username: str = "admin",
        timeout_s: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        host = host.strip().rstrip("/")
        self.base = host if host.startswith(("http://", "https://")) else f"http://{host}"
        self.username = username
        self.password = password
        self._http = httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=False, transport=transport
        )
        self._lock = asyncio.Lock()
        self.method: str | None = None  # "md5" | "sha512", detected
        self._salt = ""
        self._session_id = 0
        self._nonce = ""
        self._req_id = -1
        self._logged_in = False

    async def aclose(self) -> None:
        if self._logged_in:
            with contextlib.suppress(Exception):
                await self._request([{"id": 0, "method": "logOut"}])
        await self._http.aclose()

    # ── hashing ──────────────────────────────────────────────────────────────

    def _h(self, s: str) -> str:
        b = s.encode()
        if self.method == "sha512":
            return hashlib.sha512(b).hexdigest()
        return hashlib.md5(b).hexdigest()  # noqa: S324 - required by the Hub's firmware

    async def _detect(self) -> list[str]:
        """Hash methods to try, best guess first (the web UI says which one it uses)."""
        try:
            r = await self._http.get(f"{self.base}/gui/js/gui-core.js")
            text = r.text if r.status_code == 200 else ""
        except httpx.HTTPError:
            text = ""
        m = re.search(r"GUI_PASSWORD_SALT\s*:\s*[\"']([^\"']*)[\"']", text)
        self._salt = m.group(1) if m else ""
        if re.search(r"GUI_ACTIVATE_SHA512ENCODE_OPT\s*:\s*1", text):
            return ["sha512"]
        if text:
            return ["md5"]
        return ["sha512", "md5"]  # unknown: firmware 2.13+ on the 4000 moved to SHA-512

    # ── requests ─────────────────────────────────────────────────────────────

    async def _request(self, actions: list[dict], *, priority: bool = False) -> dict:
        self._req_id += 1
        cnonce = secrets.randbelow(500_000)
        pw = self.password + (f":{self._salt}" if self._salt else "")
        cred = self._h(f"{self.username}:{self._nonce}:{self._h(pw)}")
        auth = self._h(f"{cred}:{self._req_id}:{cnonce}:JSON:{ENDPOINT}")
        payload = {
            "request": {
                "id": self._req_id,
                "session-id": int(self._session_id),
                "priority": priority,
                "actions": actions,
                "cnonce": cnonce,
                "auth-key": auth,
            }
        }
        try:
            r = await self._http.post(
                f"{self.base}{ENDPOINT}", data={"req": json.dumps(payload, separators=(",", ":"))}
            )
        except httpx.TimeoutException as exc:
            raise HubError("the Hub did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise HubError(f"cannot reach the Hub: {exc}") from exc
        if r.status_code != 200:
            raise HubError(f"the Hub answered HTTP {r.status_code}")
        try:
            data = json.loads(r.content.decode("utf-8", "replace"))
            err = data["reply"]["error"]["description"]
        except (ValueError, KeyError, TypeError) as exc:
            raise HubError("unexpected answer from the Hub (is this a Home Hub?)") from exc
        if err == "XMO_INVALID_SESSION_ERR":
            raise _Reauth(err)
        if err == "XMO_AUTHENTICATION_ERR":
            raise HubLoginError("wrong admin password (or unsupported firmware)")
        if err == "XMO_LOGIN_RETRY_ERR":
            raise HubLoginError("the Hub is refusing sign-ins for now (too many attempts)")
        if err == "XMO_MAX_SESSION_COUNT_ERR":
            raise HubError("too many sessions open on the Hub; try again in a few minutes")
        if err not in OK and err != "XMO_REQUEST_ACTION_ERR":
            raise HubError(f"Hub error {err}")
        return data["reply"]

    async def _login(self) -> None:
        methods = await self._detect()
        last: Exception | None = None
        for method in methods:
            self.method = method
            self._session_id, self._nonce, self._req_id = 0, "", -1
            action = {
                "id": 0,
                "method": "logIn",
                "parameters": {
                    "user": self.username,
                    "persistent": True,
                    "session-options": {
                        "nss": [{"name": "gtw", "uri": "http://sagemcom.com/gateway-data"}],
                        "language": "ident",
                        "context-flags": {"get-content-name": True, "local-time": True},
                        "capability-depth": 2,
                        "capability-flags": {
                            "name": True,
                            "default-value": False,
                            "restriction": True,
                            "description": False,
                        },
                        "time-format": "ISO_8601",
                        "write-only-string": "_XMO_WRITE_ONLY_",
                        "undefined-write-only-string": "_XMO_UNDEFINED_WRITE_ONLY_",
                    },
                },
            }
            try:
                reply = await self._request([action], priority=True)
                p = reply["actions"][0]["callbacks"][0]["parameters"]
                self._session_id, self._nonce = p["id"], p["nonce"]
            except (HubLoginError, HubError, KeyError, IndexError, TypeError) as exc:
                last = exc
                if isinstance(exc, HubLoginError) and "refusing" in str(exc):
                    raise
                continue
            self._logged_in = True
            log.info("signed in to the Home Hub", extra={"method": method})
            return
        if isinstance(last, HubError):
            raise last
        raise HubLoginError("sign-in failed")

    async def get_values(self, xpaths: list[str]) -> list[Any]:
        """Values for several paths in one request; None for a path this Hub doesn't have."""
        actions = [
            {
                "id": i,
                "method": "getValue",
                "xpath": urllib.parse.quote(x, safe="/=[]'@\""),
                "options": {},
            }
            for i, x in enumerate(xpaths)
        ]
        async with self._lock:
            for attempt in (1, 2):
                try:
                    if not self._logged_in:
                        await self._login()
                    reply = await self._request(actions)
                    break
                except _Reauth:
                    self._logged_in = False
                    if attempt == 2:
                        raise HubError("the Hub keeps ending the session") from None
        out: list[Any] = []
        for i in range(len(xpaths)):
            try:
                a = reply["actions"][i]
                desc = (a.get("error") or {}).get("description", "XMO_NO_ERR")
                out.append(a["callbacks"][0]["parameters"]["value"] if desc in OK else None)
            except (KeyError, IndexError, TypeError):
                out.append(None)
        return out

    # ── what NetWatch reads ──────────────────────────────────────────────────

    async def snapshot(self) -> dict:
        info, optical, ppp, ip = await self.get_values(
            [
                "Device/DeviceInfo",
                "Device/Optical/Interfaces",
                "Device/PPP/Interfaces",
                "Device/IP/Interfaces",
            ]
        )
        if info is None:
            raise HubError("the Hub returned no device information")
        info = _unwrap(info, "DeviceInfo")
        opt = _first(_unwrap(optical, "Interfaces"))
        wan_ppp = _first([p for p in _list(_unwrap(ppp, "Interfaces")) if p.get("Enable", True)])
        wan_ip = _wan_ip(_list(_unwrap(ip, "Interfaces")))
        return {
            "model": info.get("ModelName") or info.get("ProductClass"),
            "firmware": info.get("SoftwareVersion") or info.get("ExternalFirmwareVersion"),
            "hardware": info.get("HardwareVersion"),
            "uptime_s": _int(info.get("UpTime")),
            "reboots": _int(info.get("RebootCount")),
            "optical": {
                "status": opt.get("Status"),
                "rx_dbm": optical_dbm(opt.get("OpticalSignalLevel")),
                "tx_dbm": optical_dbm(opt.get("TransmitOpticalLevel"), tx=True),
                "rx_raw": opt.get("OpticalSignalLevel"),
                "tx_raw": opt.get("TransmitOpticalLevel"),
                "rx_low": optical_dbm(opt.get("LowerOpticalThreshold")),
                "temperature": opt.get("Temperature"),
                "errors_rx": _int((opt.get("Stats") or {}).get("ErrorsReceived")),
            }
            if opt
            else None,
            "ppp": {
                "status": wan_ppp.get("ConnectionStatus") or wan_ppp.get("Status"),
                "uptime_s": _int(wan_ppp.get("LastChange")),
                "last_error": wan_ppp.get("LastConnectionError"),
            }
            if wan_ppp
            else None,
            "wan_ip": wan_ip,
        }

    async def exposure(self) -> dict:
        """What the Hub lets in from the internet: port forwards (manual and UPnP), whether UPnP
        is on, and the DMZ host. Paths a firmware lacks come back as None."""
        maps, upnp, dmz = await self.get_values(
            [
                "Device/NAT/PortMappings",
                "Device/UPnP/Device",
                "Device/Services/BellNetworkCfg/AdvancedDMZ",
            ]
        )
        dev = _first(_unwrap(upnp, "Device"))
        dmz = _first(_unwrap(dmz, "AdvancedDMZ"))
        return {
            "port_mappings": [port_mapping(m) for m in _list(_unwrap(maps, "PortMappings"))]
            if maps is not None
            else None,
            "upnp_enabled": bool(dev.get("Enable") and dev.get("UPnPIGD", True)) if dev else None,
            "dmz_host": (dmz.get("AdvancedDMZhost") or None) if dmz.get("Enable") else None,
            "dmz_known": bool(dmz),
        }

    async def explore(self, xpath: str) -> Any:
        """Raw value of any path, credentials hidden (to find fields on a new firmware)."""
        (value,) = await self.get_values([xpath])
        return redact(value)


def port_mapping(m: dict) -> dict:
    """A port forward in NetWatch's terms; ``origin`` tells UPnP (added by a device) from manual."""
    creator = " ".join(str(m.get(k) or "") for k in ("Creator", "Service", "Alias")).lower()
    desc = str(m.get("Description") or "")
    upnp = "upnp" in creator or "upnp" in desc.lower() or "miniupnp" in desc.lower()
    ext = _int(m.get("ExternalPort"))
    end = _int(m.get("ExternalPortEndRange")) or ext
    return {
        "enabled": bool(m.get("Enable", True)),
        "protocol": str(m.get("Protocol") or "TCP").upper()[:8],
        "external_port": ext,
        "external_port_end": end if end and ext and end > ext else None,
        "internal_ip": str(m.get("InternalClient") or "")[:45] or None,
        "internal_port": _int(m.get("InternalPort")) or ext,
        "description": desc[:80] or None,
        "origin": "upnp" if upnp else "manual",
        "remote_host": str(m.get("RemoteHost") or "")[:45] or None,
    }


def _unwrap(v: Any, key: str) -> Any:
    return v.get(key, v) if isinstance(v, dict) and len(v) == 1 else v


def _list(v: Any) -> list[dict]:
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    if isinstance(v, dict):
        return [v]
    return []


def _first(v: Any) -> dict:
    items = _list(v)
    return items[0] if items else {}


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _wan_ip(interfaces: list[dict]) -> str | None:
    """The public IPv4 address: the first interface address that isn't private."""
    import ipaddress

    for itf in interfaces:
        for a in _list(itf.get("IPv4Addresses")):
            try:
                ip = ipaddress.ip_address(str(a.get("IPAddress", "")))
            except ValueError:
                continue
            if ip.is_global or ip in ipaddress.ip_network("100.64.0.0/10"):
                return str(ip)
    return None
