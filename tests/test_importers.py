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
