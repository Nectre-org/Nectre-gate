"""A YAML manifest is just the Source model written out by hand."""

from __future__ import annotations

from typing import Any

from ..models import Source


def from_manifest(doc: dict[str, Any], **overrides: Any) -> Source:
    data = {**doc, **{k: v for k, v in overrides.items() if v is not None}}
    return Source.model_validate(data)
