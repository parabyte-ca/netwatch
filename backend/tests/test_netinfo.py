from netwatch.services.netinfo import is_private, parse_default_route

ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
enp3s0\t00000000\t0102A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0
enp3s0\t0002A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0
wg0\t00000000\t0100000A\t0003\t0\t0\t50\t00000000\t0\t0\t0
"""


def test_parse_default_route_picks_lowest_metric():
    r = parse_default_route(ROUTE)
    assert r is not None
    assert r.interface == "wg0"
    assert r.gateway == "10.0.0.1"


def test_parse_default_route_little_endian():
    text = ROUTE.splitlines()[0] + "\n" + ROUTE.splitlines()[1]
    r = parse_default_route(text)
    assert r.gateway == "192.168.2.1"
    assert r.interface == "enp3s0"


def test_parse_default_route_none():
    assert parse_default_route(ROUTE.splitlines()[0] + "\n" + ROUTE.splitlines()[2]) is None
    assert parse_default_route("") is None


def test_is_private():
    assert is_private("192.168.2.1")
    assert not is_private("1.1.1.1")
    assert not is_private("example.com")
