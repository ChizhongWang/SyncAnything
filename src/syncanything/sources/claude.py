from __future__ import annotations

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


class ClaudeAdapter(SourceAdapter):
    name = "claude"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.home() / ".claude" / "projects"

    def discover(self) -> Iterable[Path]:
        if not self.root.exists():
            return []
        return sorted(
            path
            for path in self.root.rglob("*.jsonl")
            if "subagents" not in path.parts
        )

    def parse(self, path: Path) -> Session | None:
        messages: list[Message] = []
        native_id = path.stem
        cwd: str | None = None
        started_at: str | None = None
        updated_at: str | None = None

        for record in read_jsonl(path):
            record_type = record.get("type")
            if record_type not in {"user", "assistant"}:
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            role = message.get("role") or record_type
            if role not in {"user", "assistant"}:
                continue
            text = visible_text(message.get("content"))
            if not is_conversation_text(text):
                continue
            timestamp = record.get("timestamp")
            timestamp = timestamp if isinstance(timestamp, str) else None
            messages.append(Message(role=role, text=text, timestamp=timestamp))
            native_id = str(record.get("sessionId") or native_id)
            cwd = record.get("cwd") if isinstance(record.get("cwd"), str) else cwd
            started_at = started_at or timestamp
            updated_at = timestamp or updated_at

        if not messages:
            return None
        fallback_time = iso_from_mtime(path)
        return Session(
            source=self.name,
            native_id=native_id,
            path=path,
            messages=messages,
            title=choose_title(messages),
            cwd=cwd,
            started_at=started_at or fallback_time,
            updated_at=updated_at or fallback_time,
        )
