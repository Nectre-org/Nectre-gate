"""Turn an OpenAPI spec or a YAML manifest into a Source."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import yaml

from ..models import Source
from .manifest import from_manifest
from .openapi import from_openapi

__all__ = ["load_document", "import_source", "from_manifest", "from_openapi"]


def load_document(location: str) -> dict[str, Any]:
    """Read JSON or YAML from a local path or an http(s) URL."""
    if location.startswith(("http://", "https://")):
        resp = httpx.get(location, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        text = resp.text
    else:
        text = Path(location).read_text(encoding="utf-8")
    doc = yaml.safe_load(text)  # YAML is a superset of JSON
    if not isinstance(doc, dict):
        raise ValueError(f"{location} is not a JSON or YAML object")
    return doc


def import_source(location: str, **overrides: Any) -> Source:
    """Detect the document kind and build a Source from it."""
    doc = load_document(location)
    if "openapi" in doc or "swagger" in doc:
        return from_openapi(doc, spec_url=location, **overrides)
    return from_manifest(doc, **overrides)
