"""Extra edge-case, failure-mode and property tests for the security hardening."""
import ipaddress
import socket

import httpcore
import httpx
import pytest
import respx
from hypothesis import given, settings, strategies as st

from nectregate import cli, executor
from nectregate.cli import _is_loopback
from nectregate.executor import CallError, _PinnedBackend, _read_capped, is_blocked_ip, vet_host
from nectregate.importers import load_document

PUB = "93.184.216.34"
ips = st.one_of(st.ip_addresses(v=4), st.ip_addresses(v=6)).map(str)


def model_blocked(a):
    ip = ipaddress.ip_address(a)
    return any([ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_reserved, ip.is_multicast])


# --- vet_host edge cases named in the spec ---
def test_vet_host_empty_list():
    with pytest.raises(CallError, match="cannot resolve host"):
        vet_host("x.com", lambda h: [])


def test_vet_host_gaierror():
    def r(h):
        raise socket.gaierror("nope")
    with pytest.raises(CallError, match="cannot resolve host 'x.com'"):
        vet_host("x.com", r)


def test_vet_host_mixed_blocks():
    with pytest.raises(CallError, match="private address; set NECTREGATE_ALLOW_PRIVATE=1"):
        vet_host("x.com", lambda h: [PUB, "10.0.0.1"])


@settings(max_examples=300, deadline=None)
@given(st.lists(ips, min_size=1, max_size=5))
def test_prop_vet_host_blocks_iff_any_blocked(addrs):
    if any(model_blocked(a) for a in addrs):
        with pytest.raises(CallError, match="private address"):
            vet_host("h", lambda h: addrs)
    else:
        assert vet_host("h", lambda h: addrs) == addrs


@settings(max_examples=300, deadline=None)
@given(ips)
def test_prop_is_blocked_matches_model(a):
    assert is_blocked_ip(a) == model_blocked(a)


@pytest.mark.parametrize("a", ["127.0.0.1", "::1", "169.254.169.254", "0.0.0.0", "::ffff:127.0.0.1", "fd00::1", "224.0.0.1"])
def test_known_blocked(a):
    assert is_blocked_ip(a)


def test_is_blocked_malformed_raises_valueerror():
    with pytest.raises(ValueError):
        is_blocked_ip("not-an-ip")


# --- pinned backend ---
class Rec(httpcore.NetworkBackend):
    def __init__(self): self.calls = []
    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.calls.append(host); return "sock"
    def sleep(self, s): pass


@settings(max_examples=200, deadline=None)
@given(ips)
def test_prop_backend_connects_only_to_vetted_public_ip(a):
    rec = Rec()
    b = _PinnedBackend(lambda h: [a], False, inner=rec)
    if model_blocked(a):
        with pytest.raises(httpcore.ConnectError):
            b.connect_tcp("h.example", 443)
        assert rec.calls == []
    else:
        assert b.connect_tcp("h.example", 443) == "sock"
        assert rec.calls == [a]


def test_backend_bytes_host_and_unix_socket():
    rec = Rec()
    b = _PinnedBackend(lambda h: [PUB], False, inner=rec)
    b.connect_tcp(b"api.example.com", 443)
    assert rec.calls == [PUB]
    with pytest.raises(httpcore.ConnectError, match="unix"):
        b.connect_unix_socket("/tmp/s")


def test_backend_dns_failure_is_connecterror():
    b = _PinnedBackend(lambda h: [], False, inner=Rec())
    with pytest.raises(httpcore.ConnectError, match="cannot resolve"):
        b.connect_tcp("h", 80)


def test_safe_client_blocks_loopback_end_to_end():
    # Real httpx client with pinned transport; resolver says 127.0.0.1 so nothing is ever dialed.
    c = executor.make_safe_client(lambda h: ["127.0.0.1"], False)
    with pytest.raises(httpx.ConnectError, match="private address"):
        c.get("http://evil.example/")


def test_safe_client_ignores_proxy_env(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    c = executor.make_safe_client(lambda h: [PUB], False)
    assert not c._mounts


# --- _read_capped property ---
@settings(max_examples=200, deadline=None)
@given(st.lists(st.binary(max_size=50), max_size=40), st.integers(-2, 2), st.integers(0, 600), st.booleans())
def test_prop_read_capped(chunks, delta, rnd, near):
    cap = max(0, len(b"".join(chunks)) + delta) if near else rnd
    old = executor.MAX_BYTES
    executor.MAX_BYTES = cap
    try:
        whole = b"".join(chunks)
        closed = []
        r = httpx.Response(200, content=iter(chunks))
        orig = r.close
        r.close = lambda: (closed.append(1), orig())
        body, cut = _read_capped(r)
        assert body == whole[:cap]
        assert cut == (len(whole) > cap)
        assert closed
    finally:
        executor.MAX_BYTES = old


def test_read_capped_closes_on_error():
    def gen():
        yield b"a"; raise RuntimeError("boom")
    r = httpx.Response(200, content=gen())
    with pytest.raises(RuntimeError):
        _read_capped(r)
    assert r.is_closed


# --- _is_loopback property ---
@settings(max_examples=300, deadline=None)
@given(st.one_of(ips, st.text(max_size=30)))
def test_prop_is_loopback(h):
    try:
        exp = ipaddress.ip_address(h).is_loopback
    except ValueError:
        exp = h == "localhost"
    assert _is_loopback(h) == exp


@pytest.mark.parametrize("h,exp", [("127.0.0.2", True), ("::", False), ("LOCALHOST", False), ("[::1]", False), ("", False)])
def test_is_loopback_cases(h, exp):
    assert _is_loopback(h) is exp


# --- importer failures ---
@respx.mock
def test_import_non_http_redirect_scheme_rejected():
    respx.get("https://spec.example.com/o.yaml").mock(return_value=httpx.Response(302, headers={"location": "file:///etc/passwd"}))
    with pytest.raises(ValueError, match="non-http"):
        load_document("https://spec.example.com/o.yaml", resolver=lambda h: [PUB])


@respx.mock
def test_import_exactly_max_redirects_ok():
    for i in range(3):
        respx.get(f"https://spec.example.com/{i}").mock(return_value=httpx.Response(302, headers={"location": f"/{i+1}"}))
    respx.get("https://spec.example.com/3").mock(return_value=httpx.Response(200, text="a: 1\n"))
    assert load_document("https://spec.example.com/0", resolver=lambda h: [PUB]) == {"a": 1}


@respx.mock
def test_import_404_is_httperror():
    respx.get("https://spec.example.com/o.yaml").mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        load_document("https://spec.example.com/o.yaml", resolver=lambda h: [PUB])


def test_import_env_allow_private(monkeypatch):
    monkeypatch.setenv("NECTREGATE_ALLOW_PRIVATE", "1")
    called = []
    with respx.mock:
        respx.get("https://spec.example.com/o.yaml").mock(return_value=httpx.Response(200, text="a: 1\n"))
        assert load_document("https://spec.example.com/o.yaml", resolver=lambda h: called.append(h) or ["127.0.0.1"]) == {"a": 1}
    assert called == []


def test_cli_add_private_url_clean_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(executor, "_default_resolver", lambda h: ["127.0.0.1"])
    import nectregate.importers as imp
    monkeypatch.setattr(imp, "_default_resolver", lambda h: ["127.0.0.1"])
    monkeypatch.delenv("NECTREGATE_ALLOW_PRIVATE", raising=False)
    with pytest.raises(SystemExit) as e:
        cli.main(["--db", str(tmp_path / "r.db"), "add", "https://spec.example.com/o.yaml"])
    assert str(e.value).startswith("error: ") and "private address" in str(e.value)


def test_read_capped_exact_length_not_cut(monkeypatch):
    monkeypatch.setattr(executor, "MAX_BYTES", 1)
    assert _read_capped(httpx.Response(200, content=iter([b"a"]))) == (b"a", False)
