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


class PiAdapter(SourceAdapter):
    name = "pi"

    def __init__(self, roots: list[Path] | None = None) -> None:
        self.roots = roots or [
            Path.home() / ".pi" / "agent" / "sessions",
            Path.home() / ".local" / "share" / "pi-coding-agent" / "sessions",
        ]

    def discover(self) -> Iterable[Path]:
        paths: set[Path] = set()
        for root in self.roots:
            if root.exists():
                paths.update(root.rglob("*.jsonl"))
        return sorted(paths)

    def parse(self, path: Path) -> Session | None:
        messages: list[Message] = []
        native_id = path.stem
        cwd: str | None = None
        started_at: str | None = None
        updated_at: str | None = None
        explicit_title: str | None = None

        for record in read_jsonl(path):
            record_type = record.get("type")
            timestamp = record.get("timestamp")
            timestamp = timestamp if isinstance(timestamp, str) else None
            if record_type == "session":
                native_id = str(record.get("id") or native_id)
                cwd = record.get("cwd") if isinstance(record.get("cwd"), str) else cwd
                started_at = timestamp or started_at
            elif record_type == "session_info" and isinstance(record.get("name"), str):
                explicit_title = record["name"]
            elif record_type == "message":
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                if role not in {"user", "assistant"}:
                    continue
                text = visible_text(message.get("content"))
                if is_conversation_text(text):
                    messages.append(Message(role=role, text=text, timestamp=timestamp))
                    updated_at = timestamp or updated_at

        if not messages:
            return None
        fallback_time = iso_from_mtime(path)
        return Session(
            source=self.name,
            native_id=native_id,
            path=path,
            messages=messages,
            title=choose_title(messages, explicit_title),
            cwd=cwd,
            started_at=started_at or fallback_time,
            updated_at=updated_at or fallback_time,
        )
