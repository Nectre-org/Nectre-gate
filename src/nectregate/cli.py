"""Command line: manage sources, try searches and calls, run the server or the routing eval."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from .executor import CallError, Executor
from .importers import import_source
from .registry import Registry
from .router import Router

DEMO_DIR = Path(__file__).resolve().parent / "demo_sources"


def _parse_kv(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"expected key=value, got {pair!r}")
        k, v = pair.split("=", 1)
        out[k] = v
    return out


def run_eval(registry: Registry, path: str, router: Router | None = None) -> dict[str, Any]:
    router = router or Router(registry)
    cases = yaml.safe_load(Path(path).read_text())
    top1 = top3 = 0
    misses = []
    for case in cases:
        keys = [m.key for m in router.search(case["question"], 3)]
        if keys[:1] == [case["expect"]]:
            top1 += 1
        if case["expect"] in keys:
            top3 += 1
        else:
            misses.append({"question": case["question"], "expect": case["expect"], "got": keys})
    n = len(cases)
    return {"mode": router.mode, "cases": n, "top1": top1 / n, "top3": top3 / n, "misses": misses}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="nectre-gate", description=__doc__)
    ap.add_argument("--db", help="registry database path (default ~/.nectre-gate/registry.db or $NECTREGATE_DB)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="register a source from an OpenAPI spec or YAML manifest (path or URL)")
    a.add_argument("location")
    a.add_argument("--id")
    a.add_argument("--name")
    a.add_argument("--base-url")
    sub.add_parser("load-demo", help="register the bundled demo sources")
    sub.add_parser("list", help="list registered sources")
    s = sub.add_parser("show", help="print a source as YAML")
    s.add_argument("id")
    r = sub.add_parser("remove", help="remove a source")
    r.add_argument("id")
    q = sub.add_parser("search", help="rank endpoints for a question")
    q.add_argument("question")
    q.add_argument("-n", type=int, default=5)
    c = sub.add_parser("call", help="call an endpoint: nectre-gate call source.endpoint key=value ...")
    c.add_argument("endpoint")
    c.add_argument("params", nargs="*")
    e = sub.add_parser("eval", help="score routing accuracy on a YAML file of {question, expect}")
    e.add_argument("file")
    v = sub.add_parser("serve", help="run the MCP server")
    v.add_argument("--http", action="store_true", help="streamable HTTP instead of stdio")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8765)

    args = ap.parse_args(argv)
    reg = Registry(args.db)

    if args.cmd == "add":
        src = import_source(args.location, id=args.id, name=args.name, base_url=args.base_url)
        reg.upsert(src)
        print(f"registered {src.id} ({len(src.endpoints)} endpoints)")
        if src.auth.type != "none":
            print(f"  needs a secret in env var {src.auth.env}")
    elif args.cmd == "load-demo":
        for f in sorted(DEMO_DIR.glob("*.yaml")):
            src = import_source(str(f))
            reg.upsert(src)
            print(f"registered {src.id} ({len(src.endpoints)} endpoints)")
    elif args.cmd == "list":
        for src in reg.all():
            print(f"{src.id:24} {len(src.endpoints):3} endpoints  {src.name}")
    elif args.cmd == "show":
        print(yaml.safe_dump(reg.get(args.id).model_dump(by_alias=True, exclude_none=True), sort_keys=False))
    elif args.cmd == "remove":
        print("removed" if reg.remove(args.id) else "not found")
    elif args.cmd == "search":
        router = Router(reg)
        for m in router.search(args.question, args.n):
            d = m.to_dict()
            print(f"{d['score']:8.3f}  {d['endpoint']:40} {d['summary']}")
    elif args.cmd == "call":
        try:
            env = Executor(reg).call(args.endpoint, _parse_kv(args.params))
        except CallError as err:
            sys.exit(f"error: {err}")
        print(json.dumps(env.model_dump(), indent=2, ensure_ascii=False))
    elif args.cmd == "eval":
        print(json.dumps(run_eval(reg, args.file), indent=2))
    elif args.cmd == "serve":
        from .server import build_server

        server = build_server(reg)
        if args.http:
            server.run("streamable-http", host=args.host, port=args.port)
        else:
            server.run()


if __name__ == "__main__":
    main()
