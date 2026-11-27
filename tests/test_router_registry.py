from pathlib import Path

from nectregate.cli import run_eval
from nectregate.registry import Registry
from nectregate.router import Router, tokenize

EVAL = Path(__file__).parent.parent / "evals" / "routing.yaml"


def test_registry_roundtrip_and_remove(demo_registry):
    assert len(demo_registry.all()) == 6
    src, ep = demo_registry.resolve("frankfurter.latest")
    assert ep.path == "/latest"
    assert demo_registry.remove("frankfurter")
    assert not demo_registry.remove("frankfurter")
    assert len(demo_registry.all()) == 5


def test_router_sees_changes_from_another_connection(demo_registry):
    router = Router(demo_registry)
    assert router.search("exchange rate")
    other = Registry(demo_registry.path)  # e.g. the CLI in another process
    other.remove("frankfurter")
    assert all(not m.key.startswith("frankfurter") for m in router.search("exchange rate"))


def test_tokenize_drops_stopwords_and_plurals():
    assert tokenize("What are the currencies of countries?") == ["currency", "country"]


def test_obvious_questions_route_correctly(demo_registry):
    router = Router(demo_registry)
    assert router.search("USD to NZD exchange rate", 1)[0].key == "frankfurter.latest"
    assert router.search("weather forecast for Nelson", 1)[0].key == "open-meteo.forecast"
    assert router.search("research papers on tool use", 1)[0].key == "openalex.search_works"


def test_no_match_returns_empty(demo_registry):
    assert Router(demo_registry).search("zzzz qqqq") == []


def test_routing_eval_floor(demo_registry):
    # BM25-only floor on held-out questions; embeddings should raise it.
    result = run_eval(demo_registry, str(EVAL))
    assert result["cases"] >= 20
    assert result["top1"] >= 0.75
    assert result["top3"] >= 0.8
