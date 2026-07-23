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


class CodexAdapter(SourceAdapter):
    name = "codex"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.home() / ".codex" / "sessions"

    def discover(self) -> Iterable[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.rglob("*.jsonl"))

    def parse(self, path: Path) -> Session | None:
        messages: list[Message] = []
        fallback_users: list[Message] = []
        native_id = path.stem.rsplit("-", 5)[-1]
        cwd: str | None = None
        started_at: str | None = None
        updated_at: str | None = None

        for record in read_jsonl(path):
            timestamp = record.get("timestamp")
            timestamp = timestamp if isinstance(timestamp, str) else None
            if record.get("type") == "session_meta":
                payload = record.get("payload")
                if isinstance(payload, dict):
                    native_id = str(payload.get("id") or payload.get("session_id") or native_id)
                    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else cwd
                    started_at = payload.get("timestamp") if isinstance(payload.get("timestamp"), str) else timestamp
                continue

            payload = record.get("payload")
            if record.get("type") == "response_item" and isinstance(payload, dict):
                if payload.get("type") != "message":
                    continue
                role = payload.get("role")
                if role not in {"user", "assistant"}:
                    continue
                text = visible_text(payload.get("content"))
                if is_conversation_text(text):
                    messages.append(Message(role=role, text=text, timestamp=timestamp))
                    updated_at = timestamp or updated_at
                continue

            # Older rollouts can expose user messages only as event_msg records.
            if record.get("type") == "event_msg" and isinstance(payload, dict):
                if (
                    payload.get("type") == "user_message"
                    and isinstance(payload.get("message"), str)
                    and is_conversation_text(payload["message"])
                ):
                    fallback_users.append(
                        Message(role="user", text=payload["message"].strip(), timestamp=timestamp)
                    )

        if not any(message.role == "user" for message in messages):
            messages = fallback_users + messages
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
            started_at=started_at or messages[0].timestamp or fallback_time,
            updated_at=updated_at or messages[-1].timestamp or fallback_time,
        )
