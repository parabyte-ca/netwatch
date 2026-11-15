"""Seed a throwaway data directory for the CI layout check.

    python scripts/ci_seed.py /tmp/nw-data    # prints the device page paths to check

Applies the migrations and adds devices with long names, a group with tags and a few events
through the real stores, so the phone layout check sees realistic, crowded pages. Never point
it at a real install.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from netwatch.db import make_engine, run_migrations
from netwatch.services.device_groups import GroupStore
from netwatch.services.discovery.inventory import DeviceStore
from netwatch.services.events import EventLog

LONG = (
    "Living-Room-Samsung-QN90B-Neo-QLED-Television",
    "Theresas-iPhone-15-Pro-Max-With-A-Very-Long-Name",
    "HP-OfficeJet-Pro-9015e-All-in-One-Printer-Upstairs",
    "Kasa-Smart-Wi-Fi-Plug-Mini-Basement-Dehumidifier",
)


def main(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    if (data_dir / "netwatch.db").exists():
        sys.exit("refusing to seed an existing database")
    engine = make_engine(data_dir / "netwatch.db")
    run_migrations(engine)
    now = int(time.time() * 1000)
    store = DeviceStore(engine)
    ids = []
    for i in range(30):
        ids.append(
            store.insert(
                {
                    "mac": f"02:00:00:00:00:{i:02x}",
                    "first_seen_ms": now - 86_400_000,
                    "last_seen_ms": now - i * 60_000,
                    "last_ip": f"192.168.2.{100 + i}",
                    "hostname": f"{LONG[i % len(LONG)]}-{i}",
                    "vendor": "Very Long Vendor Name Electronics International Holdings",
                }
            )
        )
    groups = GroupStore(engine)
    g = groups.create("Kids devices with a long group name", "violet", "child")
    groups.assign(ids[:8], g["id"])
    groups.add_tags(ids[:12], ["upstairs-bedroom", "school-tablets", "streaming"])
    events = EventLog(engine)
    events.instant(
        "anomaly", "warning", now - 3_600_000,
        f"{LONG[3]}-3 rejoined the network 5 times in an hour (it may be losing Wi-Fi)",
        details={"type": "dhcp_churn", "mac": "02:00:00:00:00:03"},
    )  # fmt: skip
    print(" ".join(f"/devices/{i}" for i in (ids[0], ids[1], ids[3])))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
