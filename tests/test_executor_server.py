import asyncio

import httpx
import pytest
import respx

from nectregate.executor import UNTRUSTED_NOTE, CallError, Executor
from nectregate.importers import import_source
from nectregate.server import build_server

from .conftest import FIXTURES

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


@pytest.fixture
def executor(demo_registry):
    return Executor(demo_registry, resolver=PUBLIC)


@respx.mock
def test_envelope_has_provenance_and_as_of(executor):
    respx.get("https://api.frankfurter.dev/v1/latest").mock(
        return_value=httpx.Response(200, json={"amount": 1.0, "base": "USD", "date": "2026-11-26", "rates": {"NZD": 1.71}})
    )
    env = executor.call("frankfurter.latest", {"base": "USD", "symbols": "NZD"})
    assert env.source["id"] == "frankfurter" and env.source["licence"]
    assert env.as_of == "2026-11-26" and env.as_of_basis == "response_field"
    assert env.request["url"] == "https://api.frankfurter.dev/v1/latest?base=USD&symbols=NZD"
    assert env.data["rates"]["NZD"] == 1.71
    assert UNTRUSTED_NOTE in env.notes


@respx.mock
def test_path_params_are_escaped(executor):
    route = respx.get(url__startswith="https://restcountries.com/v3.1/name/").mock(return_value=httpx.Response(200, json=[]))
    executor.call("rest-countries.by_name", {"name": "New Zealand/../x"})
    assert "/name/New%20Zealand%2F..%2Fx" in str(route.calls[0].request.url)


@respx.mock
def test_response_fields_trim_and_list_cap(executor):
    results = [{"name": f"Town{i}", "latitude": i, "longitude": i, "secret": "drop me"} for i in range(40)]
    respx.get(url__startswith="https://geocoding-api.open-meteo.com/").mock(
        return_value=httpx.Response(200, json={"results": results, "generationtime_ms": 1})
    )
    env = executor.call("open-meteo-geocoding.search", {"name": "Town"})
    assert set(env.data) == {"results"}
    assert len(env.data["results"]) == 25 and env.truncated
    assert "secret" not in env.data["results"][0]
    assert env.as_of_basis == "retrieved_at"


def test_param_validation(executor):
    with pytest.raises(CallError, match="missing required parameter 'latitude'"):
        executor.call("open-meteo.forecast", {"longitude": 1})
    with pytest.raises(CallError, match="must be number"):
        executor.call("open-meteo.forecast", {"latitude": "north", "longitude": 1})
    with pytest.raises(CallError, match="unknown parameter"):
        executor.call("open-meteo.forecast", {"latitude": 1, "longitude": 1, "evil": 1})
    with pytest.raises(CallError, match="no source"):
        executor.call("nope.x", {})


def test_private_hosts_blocked(demo_registry):
    ex = Executor(demo_registry, resolver=lambda h: ["127.0.0.1"])
    with pytest.raises(CallError, match="private address"):
        ex.call("frankfurter.currencies", {})


@respx.mock
def test_secret_sent_but_never_returned(registry, monkeypatch):
    registry.upsert(import_source(str(FIXTURES / "petstore.yaml"), id="pets"))
    ex = Executor(registry, resolver=PUBLIC)
    with pytest.raises(CallError, match="PETS_API_KEY"):
        ex.call("pets.list_pets", {})
    monkeypatch.setenv("PETS_API_KEY", "s3cret")
    route = respx.get(url__startswith="https://pets.example.com/v2/pets").mock(return_value=httpx.Response(200, json=[]))
    env = ex.call("pets.list_pets", {"species": "cat"})
    assert route.calls[0].request.headers["X-Api-Key"] == "s3cret"
    assert "s3cret" not in env.model_dump_json()
    with pytest.raises(CallError, match="one of"):
        ex.call("pets.list_pets", {"species": "fish"})


@respx.mock
def test_cache_and_redirects(executor):
    route = respx.get("https://api.frankfurter.dev/v1/currencies").mock(return_value=httpx.Response(200, json={"NZD": "New Zealand Dollar"}))
    executor.call("frankfurter.currencies", {})
    env = executor.call("frankfurter.currencies", {})
    assert route.call_count == 1 and "Served from cache." in env.notes
    respx.get("https://en.wikipedia.org/api/rest_v1/page/summary/X").mock(
        return_value=httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
    )
    with pytest.raises(CallError, match="not in the source's allowed_hosts"):
        executor.call("wikipedia.summary", {"title": "X"})


@respx.mock
def test_redirect_to_allowed_host_is_followed(executor):
    respx.get(url__startswith="https://restcountries.com/v3.1/name/Samoa").mock(
        return_value=httpx.Response(302, headers={"Location": "https://files-03.restcountries.com/c.json?fields=name"})
    )
    respx.get(url__startswith="https://files-03.restcountries.com/").mock(
        return_value=httpx.Response(200, json=[{"name": {"common": "Samoa"}, "population": 200000}])
    )
    env = executor.call("rest-countries.by_name", {"name": "Samoa"})
    assert env.data[0]["name"]["common"] == "Samoa"


def _call(server, name, args):
    result = asyncio.run(server.call_tool(name, args))
    return result.structured_content


@respx.mock
def test_mcp_tools_end_to_end(demo_registry):
    server = build_server(demo_registry, executor=Executor(demo_registry, resolver=PUBLIC))
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"search_sources", "describe_source", "query_source", "list_categories"}

    found = _call(server, "search_sources", {"question": "convert dollars to euros"})
    assert found["matches"][0]["endpoint"] == "frankfurter.latest"

    card = _call(server, "describe_source", {"endpoint": "frankfurter.latest"})
    assert {p["name"] for p in card["params"]} >= {"base", "symbols"}

    respx.get(url__startswith="https://api.frankfurter.dev/v1/latest").mock(
        return_value=httpx.Response(200, json={"base": "USD", "date": "2026-11-26", "rates": {"EUR": 0.9}})
    )
    env = _call(server, "query_source", {"endpoint": "frankfurter.latest", "params": {"base": "USD", "symbols": "EUR"}})
    assert env["as_of"] == "2026-11-26" and env["data"]["rates"]["EUR"] == 0.9

    cats = _call(server, "list_categories", {})
    assert cats["total_sources"] == 6


# --- security hardening -----------------------------------------------------
import httpcore  # noqa: E402

from nectregate import executor as executor_mod  # noqa: E402
from nectregate.executor import _PinnedBackend, _read_capped  # noqa: E402


class _FakeBackend(httpcore.NetworkBackend):
    def __init__(self):
        self.calls = []

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.calls.append((host, port))
        return "stream"


def test_pinned_backend_connects_to_vetted_ip():
    inner = _FakeBackend()
    backend = _PinnedBackend(PUBLIC, allow_private=False, inner=inner)
    assert backend.connect_tcp("api.example.com", 443) == "stream"
    assert inner.calls == [("93.184.216.34", 443)]


def test_pinned_backend_blocks_rebinding(demo_registry):
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])
    resolver = lambda h: next(answers)  # noqa: E731
    ex = Executor(demo_registry, resolver=resolver)
    ex._check_host("https://rebind.example.com/")  # first answer is public
    inner = _FakeBackend()
    backend = _PinnedBackend(resolver, allow_private=False, inner=inner)
    with pytest.raises(httpcore.ConnectError, match="private address"):
        backend.connect_tcp("rebind.example.com", 443)
    assert inner.calls == []


def test_pinned_backend_allow_private_skips_resolver():
    def resolver(host):
        raise AssertionError("resolver must not be called")

    inner = _FakeBackend()
    _PinnedBackend(resolver, allow_private=True, inner=inner).connect_tcp("internal.local", 80)
    assert inner.calls == [("internal.local", 80)]


def test_read_capped_stops_early(monkeypatch):
    monkeypatch.setattr(executor_mod, "MAX_BYTES", 1000)
    yields = 0

    def gen():
        nonlocal yields
        for _ in range(100):
            yields += 1
            yield b"x" * 100

    body, cut = _read_capped(httpx.Response(200, content=gen()))
    assert len(body) == 1000 and cut
    assert yields <= 11


@respx.mock
def test_oversized_body_is_cut(executor, monkeypatch):
    monkeypatch.setattr(executor_mod, "MAX_BYTES", 1000)
    respx.get("https://api.frankfurter.dev/v1/currencies").mock(return_value=httpx.Response(200, text="a" * 5000))
    env = executor.call("frankfurter.currencies", {})
    assert "Body cut at 1000 bytes." in env.notes


@respx.mock
def test_https_to_http_redirect_refused(executor):
    respx.get("https://api.frankfurter.dev/v1/currencies").mock(
        return_value=httpx.Response(302, headers={"location": "http://api.frankfurter.dev/v1/currencies"})
    )
    plain = respx.get("http://api.frankfurter.dev/v1/currencies").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(CallError, match="downgrade"):
        executor.call("frankfurter.currencies", {})
    assert not plain.called


@respx.mock
def test_cross_host_redirect_strips_auth_case_insensitively(registry):
    src = import_source(str(FIXTURES / "petstore.yaml"), id="pets")
    src = src.model_copy(update={"allowed_hosts": ["cdn.example.com"]})
    registry.upsert(src)
    ex = Executor(registry, resolver=PUBLIC)
    respx.get("https://pets.example.com/v2/pets").mock(
        return_value=httpx.Response(302, headers={"location": "https://cdn.example.com/p"})
    )
    cdn = respx.get("https://cdn.example.com/p").mock(return_value=httpx.Response(200, json=[]))
    resp = ex._fetch(src, "https://pets.example.com/v2/pets", {"x-api-key": "s3cret", "Accept": "a"})
    resp.close()
    sent = cdn.calls[0].request.headers
    assert all(k.lower() != "x-api-key" for k in sent)
    assert sent["Accept"] == "a"
