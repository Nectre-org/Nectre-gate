"""Turn an OpenAPI spec or a YAML manifest into a Source."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

import httpx
import yaml

from ..executor import (
    MAX_REDIRECTS,
    CallError,
    _default_resolver,
    allow_private_default,
    make_safe_client,
    vet_host,
)
from ..models import Source
from .manifest import from_manifest
from .openapi import from_openapi

__all__ = ["load_document", "import_source", "from_manifest", "from_openapi"]


def _fetch_text(
    location: str, client: httpx.Client, resolver: Callable[[str], list[str]], allow_private: bool
) -> str:
    """GET with the private-host check on every hop and at most MAX_REDIRECTS redirects."""
    url = location
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise ValueError(f"{location}: refusing to fetch non-http(s) URL {url!r}")
        if not allow_private:
            try:
                vet_host(parts.hostname or "", resolver)
            except CallError as e:
                raise ValueError(str(e)) from None
        resp = client.get(url)
        if resp.is_redirect:
            url = urljoin(url, resp.headers.get("location", ""))
            continue
        resp.raise_for_status()
        return resp.text
    raise ValueError(f"{location} redirected more than {MAX_REDIRECTS} times")


def load_document(
    location: str,
    *,
    client: httpx.Client | None = None,
    resolver: Callable[[str], list[str]] | None = None,
    allow_private: bool | None = None,
) -> dict[str, Any]:
    """Read JSON or YAML from a local path or an http(s) URL."""
    if location.startswith(("http://", "https://")):
        resolver = resolver or _default_resolver
        allow_private = allow_private_default() if allow_private is None else allow_private
        own = client is None
        client = client or make_safe_client(resolver, allow_private, timeout=30)
        try:
            text = _fetch_text(location, client, resolver, allow_private)
        finally:
            if own:
                client.close()
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
