from pathlib import Path

import pytest

from nectregate.cli import DEMO_DIR
from nectregate.importers import import_source
from nectregate.registry import Registry

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_embeddings(monkeypatch):
    # Keep tests deterministic and offline.
    monkeypatch.setenv("NECTREGATE_EMBEDDINGS", "0")


@pytest.fixture
def registry(tmp_path):
    return Registry(tmp_path / "reg.db")


@pytest.fixture
def demo_registry(registry):
    for f in sorted(DEMO_DIR.glob("*.yaml")):
        registry.upsert(import_source(str(f)))
    return registry
