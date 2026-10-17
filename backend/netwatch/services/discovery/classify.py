"""Vendor lookup (offline IEEE registry) and explainable device-type guesses."""

from __future__ import annotations

import re
from functools import lru_cache

from netaddr import EUI, NotRegisteredError

from .packets import mac_bytes

TYPES = (
    "router",
    "access_point",
    "network",
    "nas",
    "server",
    "computer",
    "phone",
    "tablet",
    "tv",
    "media_player",
    "speaker",
    "printer",
    "camera",
    "game_console",
    "iot",
    "unknown",
)

_SUFFIXES = re.compile(
    r"[,.]?\s*\b(co|corp|corporation|inc|incorporated|ltd|limited|llc|gmbh|ag|sa|bv|"
    r"technologies|technology|electronics|international|holdings|group)\b\.?",
    re.IGNORECASE,
)


def is_randomised(mac: str) -> bool:
    """Locally administered address: phones and laptops use these for Wi-Fi privacy."""
    return bool(mac_bytes(mac)[0] & 0x02)


@lru_cache(maxsize=4096)
def vendor_for(mac: str) -> str | None:
    if is_randomised(mac):
        return None
    try:
        org = EUI(mac).oui.registration().org
    except (NotRegisteredError, ValueError, IndexError):
        return None
    return short_vendor(org)


def short_vendor(org: str) -> str:
    """'TP-LINK TECHNOLOGIES CO.,LTD.' → 'TP-Link'; 'Apple, Inc.' → 'Apple'."""
    s = org.strip()
    for _ in range(4):
        s2 = _SUFFIXES.sub("", s).strip(" ,.")
        if s2 == s:
            break
        s = s2
    s = s or org.strip()
    if s.isupper() and len(s) > 4:
        s = "-".join(w.capitalize() if len(w) > 3 else w for w in s.split("-"))
        s = " ".join(w if len(w) <= 3 else w[0] + w[1:].lower() for w in s.split(" "))
    return _ALIASES.get(s.lower(), s)[:64]


_ALIASES = {
    "tp-link": "TP-Link",
    "tp-link systems": "TP-Link",
    "tp-link corporation": "TP-Link",
    "hewlett packard": "HP",
    "hp": "HP",
    "samsung electronics": "Samsung",
    "lg electronics": "LG",
    "sony interactive entertainment": "Sony PlayStation",
    "sagemcom broadband sas": "Sagemcom",
    "sagemcom broadband": "Sagemcom",
    "arcadyan": "Arcadyan",
    "espressif": "Espressif",
    "raspberry pi foundation": "Raspberry Pi",
    "raspberry pi trading": "Raspberry Pi",
    "intel corporate": "Intel",
}

# (pattern, type, reason) checked against hostname / mDNS name / model / friendly name.
_NAME_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bdeco\b|deco[-_ ]?[xmspe]\d", re.I), "access_point"),
    (re.compile(r"iphone", re.I), "phone"),
    (re.compile(r"ipad", re.I), "tablet"),
    (re.compile(r"android|galaxy|pixel|oneplus|xiaomi|redmi|moto[ -]?[gex]", re.I), "phone"),
    (
        re.compile(
            r"macbook|imac|mac[-_ ]?mini|mac[-_ ]?pro|laptop|desktop|\bpc\b|-pc$|thinkpad|"
            r"surface|windows|ubuntu|fedora",
            re.I,
        ),
        "computer",
    ),
    (
        re.compile(
            r"apple[-_ ]?tv|appletv|roku|fire[-_ ]?tv|aft[a-z]|chromecast|shield|bravia|"
            r"webos|\btv\b|-tv$|tizen",
            re.I,
        ),
        "tv",
    ),
    (
        re.compile(r"homepod|sonos|echo|alexa|google[-_ ]?home|nest[-_ ]?(mini|audio|hub)", re.I),
        "speaker",
    ),
    (
        re.compile(r"printer|laserjet|officejet|deskjet|envy|epson|brother|canon|^npi", re.I),
        "printer",
    ),
    (re.compile(r"playstation|\bps[45]\b|xbox|nintendo|switch", re.I), "game_console"),
    (re.compile(r"cam(era)?\b|doorbell|\bring-|wyze|reolink|hikvision|arlo|blink", re.I), "camera"),
    (re.compile(r"truenas|synology|diskstation|qnap|\bnas\b|unraid", re.I), "nas"),
    (
        re.compile(
            r"esp[-_]?\d|esp32|esp8266|tasmota|shelly|tuya|wled|sonoff|hue|lifx|kasa|"
            r"tapo|meross|wemo|ecobee|thermostat|smart[-_ ]?plug|myq",
            re.I,
        ),
        "iot",
    ),
    (re.compile(r"raspberrypi|\bpi\d?\b|proxmox|server|docker|homeassistant", re.I), "server"),
]

_SERVICE_RULES: list[tuple[str, str]] = [
    ("_googlecast._tcp", "media_player"),
    ("_airplay._tcp", "media_player"),
    ("_raop._tcp", "speaker"),
    ("_spotify-connect._tcp", "speaker"),
    ("_sonos._tcp", "speaker"),
    ("_ipp._tcp", "printer"),
    ("_ipps._tcp", "printer"),
    ("_printer._tcp", "printer"),
    ("_pdl-datastream._tcp", "printer"),
    ("_scanner._tcp", "printer"),
    ("_hap._tcp", "iot"),
    ("_hap._udp", "iot"),
    ("_matter._tcp", "iot"),
    ("_adisk._tcp", "nas"),
    ("_smb._tcp", "computer"),
    ("_companion-link._tcp", "computer"),
    ("_ssh._tcp", "server"),
]

_VENDOR_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"synology|qnap|western digital|asustor|ixsystems", re.I), "nas"),
    (
        re.compile(
            r"espressif|tuya|shelly|sonoff|itead|signify|philips lighting|lifx|ecobee|"
            r"chamberlain|ring|amazon technologies",
            re.I,
        ),
        "iot",
    ),
    (re.compile(r"sonos|bose|harman", re.I), "speaker"),
    (re.compile(r"roku|vizio|tcl|hisense|nvidia", re.I), "tv"),
    (re.compile(r"nintendo|sony playstation|microsoft.*xbox", re.I), "game_console"),
    (re.compile(r"hikvision|dahua|reolink|wyze|axis communications|amcrest", re.I), "camera"),
    (re.compile(r"brother|seiko epson|epson|canon|lexmark|xerox|kyocera", re.I), "printer"),
    (re.compile(r"ubiquiti|netgear|cisco|aruba|mikrotik|juniper|eero", re.I), "network"),
    (re.compile(r"raspberry pi|super micro|supermicro", re.I), "server"),
    (
        re.compile(r"intel|dell|lenovo|hp\b|asustek|gigabyte|micro-star|msi|realtek", re.I),
        "computer",
    ),
]

_DHCP_VENDOR_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^android-dhcp", re.I), "phone"),
    (re.compile(r"^msft", re.I), "computer"),
    (re.compile(r"^udhcp", re.I), "iot"),
]

_SSDP_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"InternetGatewayDevice|WANIPConnection", re.I), "router"),
    (re.compile(r"MediaRenderer|dial-multiscreen|roku|samsung.*tv|webos", re.I), "tv"),
    (re.compile(r"sonos|ZonePlayer", re.I), "speaker"),
    (re.compile(r"Printer", re.I), "printer"),
    (re.compile(r"NAS|Synology|DSM", re.I), "nas"),
    (re.compile(r"Xbox|PlayStation", re.I), "game_console"),
]


def classify(
    *,
    vendor: str | None,
    names: list[str],
    services: list[str],
    dhcp_vendor: str | None,
    ssdp: list[str],
    is_gateway: bool,
    is_self: bool,
    is_dhcp_server: bool,
) -> tuple[str, str]:
    """Best guess at the device type and the evidence for it ('type', 'reason')."""
    if is_self:
        return "nas", "this NetWatch host"
    if is_gateway:
        return "router", "default gateway"
    for name in names:
        for pat, t in _NAME_RULES:
            if pat.search(name):
                return t, f"name “{name}”"
    for svc in services:
        for s, t in _SERVICE_RULES:
            if svc == s:
                return t, f"advertises {s}"
    for text in ssdp:
        for pat, t in _SSDP_RULES:
            if pat.search(text):
                return t, f"UPnP “{text[:60]}”"
    if dhcp_vendor:
        for pat, t in _DHCP_VENDOR_RULES:
            if pat.search(dhcp_vendor):
                return t, f"DHCP client “{dhcp_vendor[:40]}”"
    if is_dhcp_server:
        return "router", "answers DHCP"
    if vendor:
        if vendor.lower().startswith("apple"):
            return "unknown", "Apple device (model not advertised)"
        for pat, t in _VENDOR_RULES:
            if pat.search(vendor):
                return t, f"vendor {vendor}"
    return "unknown", "no identifying traffic yet"


# Names too common to merge different randomised MACs on (iOS 16+ sends just "iPhone").
GENERIC_NAMES = frozenset(
    {
        "iphone",
        "ipad",
        "android",
        "localhost",
        "unknown",
        "galaxy",
        "macbook",
        "macbook-air",
        "macbook-pro",
        "espressif",
        "esp32",
        "linux",
        "raspberrypi",
        "dhcpcd",
        "android-dhcp",
        "chromecast",
        "watch",
        "apple-watch",
    }
)


def mergeable_name(name: str | None) -> str | None:
    if not name:
        return None
    n = name.strip().lower()
    if len(n) < 3 or n in GENERIC_NAMES or re.fullmatch(r"[0-9a-f-]{8,}", n):
        return None
    return n
