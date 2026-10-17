import pytest
from pydantic import ValidationError

from netwatch.schemas import TargetIn, valid_host


@pytest.mark.parametrize("bad", ["-oProxyCommand=x", "a b", "foo;rm", "", "x" * 300, "bad_.-"])
def test_valid_host_rejects(bad):
    with pytest.raises(ValueError):
        valid_host(bad)


def test_valid_host_normalises():
    assert valid_host("Router.Local.") == "router.local"
    assert valid_host("192.168.002.1".replace("002", "2")) == "192.168.2.1"
    assert valid_host("::1") == "::1"


def test_icmp_defaults_timeout_to_interval():
    t = TargetIn(name="gw", kind="icmp", host="192.168.2.1", interval_s=1)
    assert t.timeout_s == 1.0
    assert t.port is None and t.url is None


def test_icmp_interval_minimum():
    with pytest.raises(ValidationError):
        TargetIn(name="gw", kind="icmp", host="192.168.2.1", interval_s=0.5)


def test_dns_requires_ip_and_fills_query():
    t = TargetIn(name="d", kind="dns", host="1.1.1.1", interval_s=10)
    assert t.dns_query == "www.google.com" and t.dns_rdtype == "A"
    with pytest.raises(ValidationError):
        TargetIn(name="d", kind="dns", host="dns.google", interval_s=10)


def test_tcp_requires_port():
    with pytest.raises(ValidationError):
        TargetIn(name="t", kind="tcp", host="192.168.2.15", interval_s=30)
    t = TargetIn(name="t", kind="tcp", host="192.168.2.15", port=443, interval_s=30)
    assert t.timeout_s == 5.0


def test_http_derives_host_and_validates_scheme():
    t = TargetIn(
        name="h", kind="http", url="https://example.com/x", interval_s=60, host="ignored.x"
    )
    assert t.host == "example.com"
    with pytest.raises(ValidationError):
        TargetIn(name="h", kind="http", url="ftp://example.com", interval_s=60)
    with pytest.raises(ValidationError):
        TargetIn(name="h", kind="http", interval_s=60)


def test_timeout_cannot_exceed_interval():
    with pytest.raises(ValidationError):
        TargetIn(name="t", kind="tcp", host="h.local", port=22, interval_s=5, timeout_s=10)


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        TargetIn(name="t", kind="icmp", host="h.local", interval_s=5, auto=True)
