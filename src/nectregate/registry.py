"""SQLite-backed store of registered sources."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Endpoint, Source


def default_db_path() -> Path:
    return Path(os.environ.get("NECTREGATE_DB", Path.home() / ".nectre-gate" / "registry.db"))


class Registry:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        self._db.commit()
        self._changes = 0

    def upsert(self, source: Source) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        self._db.execute(
            "INSERT INTO sources (id, body, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at",
            (source.id, source.model_dump_json(by_alias=True), now),
        )
        self._db.commit()
        self._changes += 1

    def remove(self, source_id: str) -> bool:
        cur = self._db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        self._db.commit()
        self._changes += 1
        return cur.rowcount > 0

    @property
    def version(self) -> tuple:
        """Changes whenever sources change, including edits made by another process (e.g. the CLI)."""
        count, latest = self._db.execute("SELECT COUNT(*), MAX(updated_at) FROM sources").fetchone()
        return (self._changes, count, latest)

    def get(self, source_id: str) -> Source:
        row = self._db.execute("SELECT body FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not row:
            raise KeyError(f"no source {source_id!r}")
        return Source.model_validate_json(row[0])

    def all(self) -> list[Source]:
        rows = self._db.execute("SELECT body FROM sources ORDER BY id").fetchall()
        return [Source.model_validate_json(r[0]) for r in rows]

    def resolve(self, key: str) -> tuple[Source, Endpoint]:
        """Look up 'source_id.endpoint_id'."""
        if "." not in key:
            raise KeyError(f"endpoint key {key!r} must look like 'source.endpoint'")
        source_id, endpoint_id = key.split(".", 1)
        source = self.get(source_id)
        return source, source.endpoint(endpoint_id)
