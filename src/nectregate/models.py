"""Data models: what a registered source looks like, and what an agent gets back."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def slugify(text: str) -> str:
    """Turn any string into a lowercase slug usable as an id."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:64] or "item"


class Param(BaseModel):
    name: str
    location: Literal["query", "path", "header"] = Field("query", alias="in")
    required: bool = False
    type: Literal["string", "integer", "number", "boolean", "array"] = "string"
    description: str = ""
    default: Any = None
    enum: list[Any] | None = None

    model_config = {"populate_by_name": True}


class Auth(BaseModel):
    """How to authenticate to a source. The secret itself is read from an env var at call time."""

    type: Literal["none", "api_key", "bearer"] = "none"
    location: Literal["header", "query"] = Field("header", alias="in")
    name: str = "Authorization"  # header or query parameter name for api_key
    env: str | None = None  # environment variable that holds the secret

    model_config = {"populate_by_name": True}


class Endpoint(BaseModel):
    id: str
    method: Literal["GET"] = "GET"  # the MVP is read-only on purpose
    path: str
    summary: str
    description: str = ""
    examples: list[str] = Field(default_factory=list, description="Example questions this endpoint answers")
    params: list[Param] = Field(default_factory=list)
    response_fields: list[str] | None = Field(
        None, description="Dotted paths to keep from the response; everything else is dropped"
    )
    as_of_field: str | None = Field(None, description="Dotted path in the response holding the data's as-of date")

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(f"endpoint id {v!r} must be a lowercase slug")
        return v


class Source(BaseModel):
    id: str
    name: str
    description: str
    base_url: str
    tags: list[str] = Field(default_factory=list)
    licence: str = "unknown"
    trust: Literal["verified", "community", "unverified"] = "unverified"
    freshness: str = Field("unknown", description="How current the data is, e.g. 'live', 'daily'")
    homepage: str | None = None
    auth: Auth = Field(default_factory=Auth)
    cache_ttl: int = Field(300, description="Seconds to cache identical calls; 0 disables")
    allowed_hosts: list[str] = Field(
        default_factory=list,
        description="Extra hosts (glob patterns like '*.example.com') that redirects may go to",
    )
    endpoints: list[Endpoint]

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(f"source id {v!r} must be a lowercase slug")
        return v

    @field_validator("base_url")
    @classmethod
    def _valid_base(cls, v: str) -> str:
        if not v.startswith(("https://", "http://")):
            raise ValueError("base_url must start with http:// or https://")
        return v.rstrip("/")

    def endpoint(self, endpoint_id: str) -> Endpoint:
        for ep in self.endpoints:
            if ep.id == endpoint_id:
                return ep
        raise KeyError(f"{self.id} has no endpoint {endpoint_id!r}")


class Envelope(BaseModel):
    """Every result an agent receives. Provenance first, data last."""

    source: dict[str, Any]
    endpoint: str
    request: dict[str, Any]
    retrieved_at: str
    as_of: str
    as_of_basis: Literal["response_field", "retrieved_at"]
    status: int
    truncated: bool = False
    notes: list[str] = Field(default_factory=list)
    data: Any
