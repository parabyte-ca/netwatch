"""First-run default targets: gateway, public anchors, and DNS resolver comparison."""

from __future__ import annotations

import logging

from .netinfo import DefaultRoute
from .storage import Store

log = logging.getLogger(__name__)

SEEDED_KEY = "seeded_defaults"
PUBLIC_RESOLVERS = (("Cloudflare", "1.1.1.1"), ("Google", "8.8.8.8"))


def default_targets(route: DefaultRoute | None) -> list[dict]:
    base = {"enabled": True, "auto": True}
    out: list[dict] = []
    if route is not None:
        out.append(
            {
                **base,
                "name": "Gateway",
                "kind": "icmp",
                "host": route.gateway,
                "interval_s": 1.0,
                "timeout_s": 1.0,
                "group": "gateway",
            }
        )
    for label, ip in PUBLIC_RESOLVERS:
        out.append(
            {
                **base,
                "name": f"{label} {ip}",
                "kind": "icmp",
                "host": ip,
                "interval_s": 2.0,
                "timeout_s": 2.0,
                "group": "public",
            }
        )
    resolvers = ([("Gateway", route.gateway)] if route else []) + list(PUBLIC_RESOLVERS)
    for label, ip in resolvers:
        out.append(
            {
                **base,
                "name": f"DNS via {label}",
                "kind": "dns",
                "host": ip,
                "dns_query": "www.google.com",
                "dns_rdtype": "A",
                "interval_s": 10.0,
                "timeout_s": 3.0,
                "group": "dns",
            }
        )
    return out


def seed_defaults(store: Store, route: DefaultRoute | None) -> int:
    """Insert the defaults once. Never re-adds targets the user later deleted."""
    if store.kv_get(SEEDED_KEY, False):
        return 0
    if route is None:
        log.warning("no IPv4 default route found; gateway target not created")
    created = 0
    if store.count_targets() == 0:
        for values in default_targets(route):
            store.create_target(values)
            created += 1
    store.kv_set(SEEDED_KEY, True)
    log.info("seeded default targets", extra={"created": created})
    return created
