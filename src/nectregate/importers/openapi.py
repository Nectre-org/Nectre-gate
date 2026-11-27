"""Import GET operations from an OpenAPI 3.x or Swagger 2.0 document."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from ..models import Auth, Endpoint, Param, Source, slugify

_TYPES = {"string", "integer", "number", "boolean", "array"}


def _deref(doc: dict[str, Any], node: Any, depth: int = 0) -> Any:
    """Resolve local '#/...' references (enough for parameters and schemas)."""
    if depth > 10 or not isinstance(node, dict) or "$ref" not in node:
        return node
    ref = node["$ref"]
    if not ref.startswith("#/"):
        return {}
    target: Any = doc
    for part in ref[2:].split("/"):
        target = target.get(part.replace("~1", "/").replace("~0", "~"), {}) if isinstance(target, dict) else {}
    return _deref(doc, target, depth + 1)


def _base_url(doc: dict[str, Any], spec_url: str | None) -> str:
    if "swagger" in doc:
        scheme = (doc.get("schemes") or ["https"])[0]
        host = doc.get("host", "")
        return f"{scheme}://{host}{doc.get('basePath', '')}".rstrip("/")
    servers = doc.get("servers") or [{"url": "/"}]
    url = servers[0].get("url", "/")
    for name, var in (servers[0].get("variables") or {}).items():
        url = url.replace("{" + name + "}", str(var.get("default", "")))
    if not url.startswith(("http://", "https://")):
        if not spec_url or not spec_url.startswith(("http://", "https://")):
            raise ValueError("spec has a relative server URL; pass base_url explicitly")
        url = urljoin(spec_url, url)
    return url.rstrip("/")


def _param(doc: dict[str, Any], raw: dict[str, Any]) -> Param | None:
    raw = _deref(doc, raw)
    loc = raw.get("in")
    if loc not in ("query", "path", "header"):
        return None
    schema = _deref(doc, raw.get("schema") or raw)  # Swagger 2 keeps type on the param itself
    ptype = schema.get("type", "string")
    return Param(
        name=raw["name"],
        location=loc,
        required=bool(raw.get("required", loc == "path")),
        type=ptype if ptype in _TYPES else "string",
        description=(raw.get("description") or "").strip(),
        default=schema.get("default"),
        enum=schema.get("enum"),
    )


def _auth(doc: dict[str, Any], source_id: str) -> Auth:
    schemes = (doc.get("components") or {}).get("securitySchemes") or doc.get("securityDefinitions") or {}
    env = f"{source_id.upper().replace('-', '_')}_API_KEY"
    for scheme in schemes.values():
        scheme = _deref(doc, scheme)
        if scheme.get("type") == "apiKey" and scheme.get("in") in ("header", "query"):
            return Auth(type="api_key", location=scheme["in"], name=scheme["name"], env=env)
        if scheme.get("type") == "http" and scheme.get("scheme", "").lower() == "bearer":
            return Auth(type="bearer", env=env)
    return Auth()


def from_openapi(
    doc: dict[str, Any],
    spec_url: str | None = None,
    id: str | None = None,
    name: str | None = None,
    base_url: str | None = None,
    **extra: Any,
) -> Source:
    info = doc.get("info") or {}
    title = name or info.get("title") or "Unnamed API"
    source_id = id or slugify(title)
    endpoints: list[Endpoint] = []
    seen: set[str] = set()

    for path, item in (doc.get("paths") or {}).items():
        item = _deref(doc, item)
        op = item.get("get")
        if not isinstance(op, dict) or op.get("deprecated"):
            continue
        params: dict[tuple[str, str], Param] = {}
        for raw in (item.get("parameters") or []) + (op.get("parameters") or []):
            p = _param(doc, raw)
            if p:
                params[(p.location, p.name)] = p  # operation-level overrides path-level
        ep_id = slugify(op.get("operationId") or f"get_{path}")
        while ep_id in seen:
            ep_id += "_2"
        seen.add(ep_id)
        summary = (op.get("summary") or op.get("description") or f"GET {path}").strip().split("\n")[0]
        endpoints.append(
            Endpoint(
                id=ep_id,
                path=path,
                summary=summary[:200],
                description=(op.get("description") or "").strip()[:1000],
                params=list(params.values()),
            )
        )

    if not endpoints:
        raise ValueError("no GET operations found in this spec")

    tags = sorted({t for item in (doc.get("paths") or {}).values() for t in ((item.get("get") or {}).get("tags") or [])})
    data: dict[str, Any] = dict(
        id=source_id,
        name=title,
        description=(info.get("description") or title).strip()[:1000],
        base_url=base_url or _base_url(doc, spec_url),
        tags=tags[:20],
        licence=(info.get("license") or {}).get("name", "unknown"),
        auth=_auth(doc, source_id),
        endpoints=endpoints,
    )
    data.update({k: v for k, v in extra.items() if v is not None})
    return Source.model_validate(data)
