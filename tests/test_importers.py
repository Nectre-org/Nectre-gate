import pytest
from pydantic import ValidationError

from nectregate.cli import DEMO_DIR
from nectregate.importers import from_openapi, import_source, load_document

from .conftest import FIXTURES


def test_openapi_import_reads_get_operations_only():
    src = import_source(str(FIXTURES / "petstore.yaml"))
    assert src.id == "pet_store"
    assert src.base_url == "https://pets.example.com/v2"
    assert [e.id for e in src.endpoints] == ["list_pets", "show_pet_by_id"]  # no POST, no deprecated
    assert src.licence == "MIT"


def test_openapi_params_refs_and_path_level_params():
    src = import_source(str(FIXTURES / "petstore.yaml"))
    listing = src.endpoint("list_pets")
    limit = next(p for p in listing.params if p.name == "limit")
    assert (limit.type, limit.default) == ("integer", 10)
    species = next(p for p in listing.params if p.name == "species")
    assert species.enum == ["cat", "dog"]
    pet = src.endpoint("show_pet_by_id").params[0]
    assert (pet.name, pet.location, pet.required) == ("petId", "path", True)


def test_openapi_auth_maps_to_env_var():
    src = import_source(str(FIXTURES / "petstore.yaml"), id="pets")
    assert src.auth.type == "api_key"
    assert src.auth.name == "X-Api-Key"
    assert src.auth.env == "PETS_API_KEY"


def test_swagger2_base_url():
    doc = {
        "swagger": "2.0",
        "info": {"title": "Old API"},
        "host": "api.example.org",
        "basePath": "/v1",
        "schemes": ["https"],
        "paths": {"/things": {"get": {"summary": "Things", "parameters": [{"name": "q", "in": "query", "type": "string"}]}}},
    }
    src = from_openapi(doc)
    assert src.base_url == "https://api.example.org/v1"
    assert src.endpoints[0].params[0].name == "q"


def test_relative_server_needs_base_url():
    doc = load_document(str(FIXTURES / "petstore.yaml"))
    doc["servers"] = [{"url": "/api"}]
    with pytest.raises(ValueError, match="relative"):
        from_openapi(doc)
    assert from_openapi(doc, base_url="https://x.example.com/api").base_url == "https://x.example.com/api"


def test_all_demo_manifests_are_valid():
    files = sorted(DEMO_DIR.glob("*.yaml"))
    assert len(files) >= 5
    for f in files:
        src = import_source(str(f))
        assert src.endpoints and src.licence != "unknown"


def test_bad_manifest_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("id: Bad Id\nname: x\ndescription: x\nbase_url: ftp://x\nendpoints: []\n")
    with pytest.raises(ValidationError):
        import_source(str(p))


# --- SSRF protection for URL imports ----------------------------------------
import httpx  # noqa: E402
import respx  # noqa: E402

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731
LOCAL = lambda host: ["127.0.0.1"]  # noqa: E731
YAML_DOC = "id: x\nname: X\nbase_url: https://x.example.com\nendpoints: []\n"


def test_load_document_blocks_private_host():
    with pytest.raises(ValueError, match="private address"):
        load_document("https://spec.example.com/o.yaml", resolver=LOCAL)


@respx.mock
def test_load_document_allow_private():
    respx.get("https://spec.example.com/o.yaml").mock(return_value=httpx.Response(200, text=YAML_DOC))
    doc = load_document("https://spec.example.com/o.yaml", resolver=LOCAL, allow_private=True)
    assert doc["id"] == "x"


@respx.mock
def test_load_document_redirect_limit():
    for i in range(5):
        respx.get(f"https://spec.example.com/{i}").mock(
            return_value=httpx.Response(302, headers={"location": f"https://spec.example.com/{i + 1}"})
        )
    with pytest.raises(ValueError, match="redirected more than"):
        load_document("https://spec.example.com/0", client=httpx.Client(), resolver=PUBLIC)


@respx.mock
def test_load_document_redirect_to_metadata_blocked():
    respx.get("https://spec.example.com/o.yaml").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/"})
    )
    meta = respx.get("http://169.254.169.254/").mock(return_value=httpx.Response(200, text=YAML_DOC))
    resolver = lambda h: ["169.254.169.254"] if h == "169.254.169.254" else ["93.184.216.34"]  # noqa: E731
    with pytest.raises(ValueError, match="private address"):
        load_document("https://spec.example.com/o.yaml", resolver=resolver)
    assert not meta.called


@respx.mock
def test_load_document_refuses_https_to_http_downgrade():
    respx.get("https://spec.example.com/o.yaml").mock(
        return_value=httpx.Response(302, headers={"location": "http://spec.example.com/o.yaml"})
    )
    plain = respx.get("http://spec.example.com/o.yaml").mock(return_value=httpx.Response(200, text=YAML_DOC))
    with pytest.raises(ValueError, match="refusing to downgrade"):
        load_document("https://spec.example.com/o.yaml", resolver=PUBLIC)
    assert not plain.called


@respx.mock
def test_load_document_rejects_oversized_spec(monkeypatch):
    import nectregate.importers as imp

    monkeypatch.setattr(imp, "MAX_SPEC_BYTES", 10)
    respx.get("https://spec.example.com/big.yaml").mock(return_value=httpx.Response(200, text="a: " + "x" * 100))
    with pytest.raises(ValueError, match="larger than 10 bytes"):
        load_document("https://spec.example.com/big.yaml", resolver=PUBLIC)


def test_pinned_transport_keeps_default_pool_limits():
    from nectregate.executor import DEFAULT_POOL_LIMITS, _PinnedTransport

    pool = _PinnedTransport(PUBLIC, allow_private=False)._pool
    limits = DEFAULT_POOL_LIMITS
    assert limits.max_connections == 100
    assert pool._max_connections == limits.max_connections
    assert pool._max_keepalive_connections == limits.max_keepalive_connections
    assert pool._keepalive_expiry == limits.keepalive_expiry
