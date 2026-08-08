from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from syncanything.connections import syncanything_home
from syncanything.models import Message, Session
from syncanything.sources.base import SourceAdapter, choose_title

_BUBBLE_TYPE_USER = 1
_BUBBLE_TYPE_ASSISTANT = 2


def _cursor_data_root() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Cursor"
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Roaming" / "Cursor"
    return Path.home() / ".config" / "Cursor"


def _find_state_dbs() -> list[Path]:
    root = _cursor_data_root() / "User"
    if not root.exists():
        return []
    candidates = [root / "globalStorage" / "state.vscdb"]
    workspace_storage = root / "workspaceStorage"
    if workspace_storage.exists():
        for workspace in workspace_storage.iterdir():
            candidates.append(workspace / "state.vscdb")
    return [path for path in candidates if path.exists()]


def _ms_to_iso(ms: int | None) -> str | None:
    if not isinstance(ms, int) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


class CursorAdapter(SourceAdapter):
    name = "cursor"

    def __init__(self, cache_root: Path | None = None) -> None:
        self.cache_root = cache_root or syncanything_home() / "connectors" / "cursor"

    def discover(self) -> Iterable[Path]:
        self._sync_from_databases()
        if not self.cache_root.exists():
            return []
        return sorted(self.cache_root.glob("composer-*.json"))

    def _sync_from_databases(self) -> None:
        seen_ids: set[str] = set()
        for db_path in _find_state_dbs():
            try:
                self._extract_composers(db_path, seen_ids)
            except (sqlite3.Error, OSError):
                continue
        if not self.cache_root.exists():
            return
        for cached in self.cache_root.glob("composer-*.json"):
            composer_id = cached.stem.split("composer-", 1)[-1]
            if composer_id not in seen_ids:
                cached.unlink(missing_ok=True)

    def _extract_composers(self, db_path: Path, seen_ids: set[str]) -> None:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT composerId, lastUpdatedAt, value "
                "FROM composerHeaders "
                "WHERE isSubagent = 0 AND composerId != 'empty-state-draft'"
            ).fetchall()
            for row in rows:
                composer_id = row["composerId"]
                if composer_id in seen_ids:
                    continue
                seen_ids.add(composer_id)
                last_updated = row["lastUpdatedAt"]
                cache_path = self.cache_root / f"composer-{composer_id}.json"
                if cache_path.exists():
                    try:
                        cached = json.loads(cache_path.read_text(encoding="utf-8"))
                        if isinstance(cached, dict) and cached.get("lastUpdatedAt") == last_updated:
                            continue
                    except (OSError, json.JSONDecodeError):
                        pass
                header = json.loads(row["value"]) if row["value"] else {}
                if not isinstance(header, dict):
                    header = {}
                bubbles = connection.execute(
                    "SELECT value FROM cursorDiskKV WHERE key LIKE ?",
                    (f"bubbleId:{composer_id}:%",),
                ).fetchall()
                bubble_data = []
                for bubble_row in bubbles:
                    try:
                        bubble = json.loads(bubble_row["value"])
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(bubble, dict):
                        bubble_data.append(bubble)
                if not bubble_data:
                    if cache_path.exists():
                        cache_path.unlink(missing_ok=True)
                    continue
                snapshot = {
                    "composerId": composer_id,
                    "name": header.get("name", ""),
                    "lastUpdatedAt": last_updated,
                    "createdAt": header.get("createdAt"),
                    "unifiedMode": header.get("unifiedMode", ""),
                    "bubbles": bubble_data,
                }
                self.cache_root.mkdir(parents=True, exist_ok=True)
                encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True) + "\n"
                temporary = cache_path.with_suffix(".json.tmp")
                temporary.write_text(encoded, encoding="utf-8")
                temporary.replace(cache_path)
        finally:
            connection.close()

    def parse(self, path: Path) -> Session | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        composer_id = payload.get("composerId", path.stem.split("composer-", 1)[-1])
        bubbles = payload.get("bubbles", [])
        if not isinstance(bubbles, list):
            return None

        timed: list[tuple[str, Message]] = []
        for bubble in bubbles:
            if not isinstance(bubble, dict):
                continue
            bubble_type = bubble.get("type")
            if bubble_type == _BUBBLE_TYPE_USER:
                role = "user"
            elif bubble_type == _BUBBLE_TYPE_ASSISTANT:
                role = "assistant"
            else:
                continue
            if bubble.get("capabilityType") is not None:
                continue
            text = bubble.get("text", "")
            if not isinstance(text, str) or not text.strip():
                continue
            timestamp = bubble.get("createdAt", "")
            timed.append((timestamp, Message(role=role, text=text.strip(), timestamp=timestamp or None)))

        timed.sort(key=lambda pair: pair[0])
        messages = [message for _, message in timed]

        if not messages:
            return None
        started_at = _ms_to_iso(payload.get("createdAt")) or (messages[0].timestamp if messages else None)
        updated_at = _ms_to_iso(payload.get("lastUpdatedAt")) or (messages[-1].timestamp if messages else None)
        return Session(
            source=self.name,
            native_id=composer_id,
            path=path,
            messages=messages,
            title=choose_title(messages, payload.get("name") or None),
            started_at=started_at,
            updated_at=updated_at,
        )
