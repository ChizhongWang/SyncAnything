from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from syncanything.connections import syncanything_home
from syncanything.models import Message, Session
from syncanything.sources.base import SourceAdapter, choose_title, read_jsonl, visible_text

_BUBBLE_TYPE_USER = 1
_BUBBLE_TYPE_ASSISTANT = 2
_USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL | re.IGNORECASE)
_TIMESTAMP_RE = re.compile(r"<timestamp>\s*.*?\s*</timestamp>\s*", re.DOTALL | re.IGNORECASE)


def _cursor_data_root() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Cursor"
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Roaming" / "Cursor"
    return Path.home() / ".config" / "Cursor"


def _cursor_cli_chats_root() -> Path:
    return Path.home() / ".cursor" / "chats"


def _cursor_projects_root() -> Path:
    return Path.home() / ".cursor" / "projects"


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


def _clean_cli_user_text(text: str) -> str:
    """Prefer the <user_query> body when Cursor CLI wraps the turn."""
    match = _USER_QUERY_RE.search(text)
    if match:
        return match.group(1).strip()
    cleaned = _TIMESTAMP_RE.sub("", text).strip()
    return cleaned or text.strip()


def _write_cache(cache_path: Path, snapshot: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True) + "\n"
    temporary = cache_path.with_suffix(".json.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(cache_path)


def _cached_matches(cache_path: Path, last_updated: Any) -> bool:
    if not cache_path.exists():
        return False
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(cached, dict) and cached.get("lastUpdatedAt") == last_updated


class CursorAdapter(SourceAdapter):
    """Index Cursor App composers and Cursor CLI agent chats as one source."""

    name = "cursor"

    def __init__(
        self,
        cache_root: Path | None = None,
        *,
        chats_root: Path | None = None,
        projects_root: Path | None = None,
        state_dbs: list[Path] | None = None,
    ) -> None:
        self.cache_root = cache_root or syncanything_home() / "connectors" / "cursor"
        self.chats_root = chats_root if chats_root is not None else _cursor_cli_chats_root()
        self.projects_root = projects_root if projects_root is not None else _cursor_projects_root()
        self.state_dbs = state_dbs

    def discover(self) -> Iterable[Path]:
        seen_ids: set[str] = set()
        self._sync_from_databases(seen_ids)
        self._sync_from_cli_chats(seen_ids)
        if not self.cache_root.exists():
            return []
        cached = list(self.cache_root.glob("composer-*.json")) + list(self.cache_root.glob("cli-*.json"))
        return sorted(cached)

    def _sync_from_databases(self, seen_ids: set[str]) -> None:
        db_paths = self.state_dbs if self.state_dbs is not None else _find_state_dbs()
        for db_path in db_paths:
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
                if _cached_matches(cache_path, last_updated):
                    continue
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
                    "kind": "app",
                    "composerId": composer_id,
                    "name": header.get("name", ""),
                    "lastUpdatedAt": last_updated,
                    "createdAt": header.get("createdAt"),
                    "unifiedMode": header.get("unifiedMode", ""),
                    "bubbles": bubble_data,
                }
                _write_cache(cache_path, snapshot)
        finally:
            connection.close()

    def _sync_from_cli_chats(self, seen_ids: set[str]) -> None:
        cli_ids: set[str] = set()
        if self.chats_root.exists():
            for meta_path in sorted(self.chats_root.glob("*/*/meta.json")):
                session_dir = meta_path.parent
                session_id = session_dir.name
                if session_id in seen_ids or session_id in cli_ids:
                    continue
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(meta, dict) or not meta.get("hasConversation", True):
                    continue
                last_updated = meta.get("updatedAtMs") or meta.get("createdAtMs")
                cache_path = self.cache_root / f"cli-{session_id}.json"
                if _cached_matches(cache_path, last_updated):
                    cli_ids.add(session_id)
                    seen_ids.add(session_id)
                    continue
                messages = self._cli_messages(session_id, session_dir)
                if not messages:
                    if cache_path.exists():
                        cache_path.unlink(missing_ok=True)
                    continue
                snapshot = {
                    "kind": "cli",
                    "sessionId": session_id,
                    "name": meta.get("title") or "",
                    "cwd": meta.get("cwd"),
                    "createdAt": meta.get("createdAtMs"),
                    "lastUpdatedAt": last_updated,
                    "messages": messages,
                }
                _write_cache(cache_path, snapshot)
                cli_ids.add(session_id)
                seen_ids.add(session_id)

        if not self.cache_root.exists():
            return
        for cached in self.cache_root.glob("cli-*.json"):
            session_id = cached.stem.split("cli-", 1)[-1]
            if session_id not in cli_ids:
                cached.unlink(missing_ok=True)

    def _cli_messages(self, session_id: str, session_dir: Path) -> list[dict[str, str | None]]:
        transcript = self._find_cli_transcript(session_id)
        if transcript is not None:
            messages = self._messages_from_transcript(transcript)
            if messages:
                return messages
        store_path = session_dir / "store.db"
        if store_path.exists():
            return self._messages_from_store(store_path)
        return []

    def _find_cli_transcript(self, session_id: str) -> Path | None:
        if not self.projects_root.exists():
            return None
        direct = list(self.projects_root.glob(f"*/agent-transcripts/{session_id}/{session_id}.jsonl"))
        if direct:
            return direct[0]
        matches = list(self.projects_root.glob(f"**/agent-transcripts/{session_id}/{session_id}.jsonl"))
        return matches[0] if matches else None

    def _messages_from_transcript(self, path: Path) -> list[dict[str, str | None]]:
        messages: list[dict[str, str | None]] = []
        for record in read_jsonl(path):
            role = record.get("role")
            if role not in {"user", "assistant"}:
                continue
            payload = record.get("message")
            content = payload.get("content") if isinstance(payload, dict) else record.get("content")
            text = visible_text(content)
            if not text:
                continue
            if role == "user":
                text = _clean_cli_user_text(text)
            if not text:
                continue
            messages.append({"role": role, "text": text, "timestamp": None})
        return messages

    def _messages_from_store(self, store_path: Path) -> list[dict[str, str | None]]:
        """Best-effort fallback when agent-transcript JSONL is missing.

        Cursor CLI store.db is content-addressed and partially protobuf-wrapped.
        Only fully UTF-8 JSON blobs with visible user/assistant text are kept.
        """
        messages: list[dict[str, str | None]] = []
        try:
            connection = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return []
        try:
            rows = connection.execute("SELECT data FROM blobs").fetchall()
        except sqlite3.Error:
            connection.close()
            return []
        for (data,) in rows:
            if not isinstance(data, (bytes, bytearray, memoryview)):
                continue
            raw = bytes(data)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            text = text.strip()
            if not text.startswith("{"):
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            role = payload.get("role")
            if role not in {"user", "assistant"}:
                continue
            body = visible_text(payload.get("content"))
            if not body:
                continue
            if role == "user":
                body = _clean_cli_user_text(body)
            if not body:
                continue
            # Skip giant system-like user dumps that are not the visible query.
            if role == "user" and body.lstrip().startswith("<user_info>"):
                continue
            messages.append({"role": role, "text": body, "timestamp": None})
        connection.close()
        return messages

    def parse(self, path: Path) -> Session | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        kind = payload.get("kind")
        if kind == "cli" or (
            "sessionId" in payload and "messages" in payload and "bubbles" not in payload
        ):
            return self._parse_cli(path, payload)
        return self._parse_app(path, payload)

    def _parse_app(self, path: Path, payload: dict[str, Any]) -> Session | None:
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
            native_id=str(composer_id),
            path=path,
            messages=messages,
            title=choose_title(messages, payload.get("name") or None),
            started_at=started_at,
            updated_at=updated_at,
            metadata={"cursor_surface": "app"},
        )

    def _parse_cli(self, path: Path, payload: dict[str, Any]) -> Session | None:
        session_id = str(payload.get("sessionId") or path.stem.split("cli-", 1)[-1])
        raw_messages = payload.get("messages", [])
        if not isinstance(raw_messages, list):
            return None
        messages: list[Message] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            text = item.get("text")
            if role not in {"user", "assistant"} or not isinstance(text, str) or not text.strip():
                continue
            timestamp = item.get("timestamp")
            messages.append(
                Message(
                    role=role,
                    text=text.strip(),
                    timestamp=timestamp if isinstance(timestamp, str) else None,
                )
            )
        if not messages:
            return None
        cwd = payload.get("cwd")
        return Session(
            source=self.name,
            native_id=session_id,
            path=path,
            messages=messages,
            title=choose_title(messages, payload.get("name") or None),
            cwd=cwd if isinstance(cwd, str) else None,
            started_at=_ms_to_iso(payload.get("createdAt")),
            updated_at=_ms_to_iso(payload.get("lastUpdatedAt")),
            metadata={"cursor_surface": "cli"},
        )
