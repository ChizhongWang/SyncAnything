from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from syncanything.models import Message, Session
from syncanything.sources.base import (
    SourceAdapter,
    choose_title,
    is_conversation_text,
    iso_from_mtime,
    read_jsonl,
    visible_text,
)


class KimiAdapter(SourceAdapter):
    name = "kimi"

    def __init__(self, roots: list[Path] | None = None) -> None:
        self.roots = roots or [Path.home() / ".kimi", Path.home() / ".kimi-code"]

    def discover(self) -> Iterable[Path]:
        paths: set[Path] = set()
        for root in self.roots:
            legacy = root / "sessions"
            if not legacy.exists():
                continue
            paths.update(legacy.glob("*/*/context.jsonl"))
            paths.update(legacy.glob("*/*/agents/main/wire.jsonl"))
        return sorted(paths)

    def parse(self, path: Path) -> Session | None:
        if path.name == "context.jsonl":
            return self._parse_legacy(path)
        return self._parse_current(path)

    def _parse_legacy(self, path: Path) -> Session | None:
        messages: list[Message] = []
        for record in read_jsonl(path):
            role = record.get("role")
            if role not in {"user", "assistant"}:
                continue
            text = visible_text(record.get("content"))
            if is_conversation_text(text):
                messages.append(Message(role=role, text=text))
        if not messages:
            return None
        timestamp = iso_from_mtime(path)
        return Session(
            source=self.name,
            native_id=path.parent.name,
            path=path,
            messages=messages,
            title=choose_title(messages),
            started_at=timestamp,
            updated_at=timestamp,
            metadata={"format": "legacy-context"},
        )

    def _parse_current(self, path: Path) -> Session | None:
        messages: list[Message] = []
        for record in read_jsonl(path):
            nested = record.get("message")
            if not isinstance(nested, dict):
                continue
            role = nested.get("role")
            if role not in {"user", "assistant"}:
                continue
            text = visible_text(nested.get("content"))
            if not is_conversation_text(text):
                continue
            raw_timestamp = record.get("timestamp") or nested.get("timestamp")
            timestamp = str(raw_timestamp) if raw_timestamp is not None else None
            messages.append(Message(role=role, text=text, timestamp=timestamp))
        if not messages:
            return None

        session_dir = path.parents[2]
        state_path = session_dir / "state.json"
        state: dict = {}
        if state_path.exists():
            try:
                loaded = json.loads(state_path.read_text(encoding="utf-8"))
                state = loaded if isinstance(loaded, dict) else {}
            except (OSError, json.JSONDecodeError):
                pass
        fallback_time = iso_from_mtime(path)
        return Session(
            source=self.name,
            native_id=session_dir.name,
            path=path,
            messages=messages,
            title=choose_title(messages, state.get("title")),
            cwd=state.get("cwd") if isinstance(state.get("cwd"), str) else None,
            started_at=str(state.get("created_at") or fallback_time),
            updated_at=str(state.get("updated_at") or fallback_time),
            metadata={"format": "wire"},
        )
