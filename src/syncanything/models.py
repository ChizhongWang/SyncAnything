from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Message:
    role: str
    text: str
    timestamp: str | None = None


@dataclass(slots=True)
class Session:
    source: str
    native_id: str
    path: Path
    messages: list[Message]
    title: str = "Untitled session"
    cwd: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.source}:{self.native_id}"
