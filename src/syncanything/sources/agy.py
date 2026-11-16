from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from syncanything.models import Message, Session
from syncanything.sources.base import (
    SourceAdapter,
    choose_title,
    is_conversation_text,
    iso_from_mtime,
    read_jsonl,
)

USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


class AgyAdapter(SourceAdapter):
    """Antigravity CLI, invoked as `agy`."""

    name = "agy"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.home() / ".gemini" / "antigravity-cli" / "brain"

    def discover(self) -> Iterable[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.glob("*/.system_generated/logs/transcript.jsonl"))

    def parse(self, path: Path) -> Session | None:
        messages: list[Message] = []
        native_id = _conversation_id(path)
        started_at: str | None = None
        updated_at: str | None = None

        for record in read_jsonl(path):
            role = _visible_role(record)
            text = _visible_text(record) if role else ""
            if not role or not is_conversation_text(text):
                continue
            timestamp = record.get("created_at")
            timestamp = timestamp if isinstance(timestamp, str) else None
            messages.append(Message(role=role, text=text, timestamp=timestamp))
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
            cwd=_workspace(path, native_id),
            started_at=started_at or fallback_time,
            updated_at=updated_at or fallback_time,
        )


def _conversation_id(path: Path) -> str:
    if path.name == "transcript.jsonl" and path.parent.name == "logs" and len(path.parents) > 2:
        return path.parents[2].name
    return path.stem


def _visible_role(record: dict) -> str | None:
    source = record.get("source")
    record_type = record.get("type")
    if source == "USER_EXPLICIT" and record_type == "USER_INPUT":
        return "user"
    if source == "MODEL" and record_type == "PLANNER_RESPONSE":
        return "assistant"
    return None


def _visible_text(record: dict) -> str:
    content = record.get("content")
    if not isinstance(content, str):
        return ""
    if record.get("source") == "USER_EXPLICIT":
        requests = [part.strip() for part in USER_REQUEST_RE.findall(content) if part.strip()]
        return "\n\n".join(requests)
    return content.strip()


def _workspace(path: Path, conversation_id: str) -> str | None:
    if len(path.parents) < 5 or path.parents[3].name != "brain":
        return None
    history = path.parents[4] / "history.jsonl"
    if not history.is_file():
        return None
    workspace: str | None = None
    for record in read_jsonl(history):
        if record.get("conversationId") != conversation_id:
            continue
        value = record.get("workspace")
        if isinstance(value, str) and value.strip():
            workspace = value
    return workspace
