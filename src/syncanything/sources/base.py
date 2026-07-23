from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable, Iterator

from syncanything.models import Message, Session


SPACE_RE = re.compile(r"[ \t]+")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LEADING_PATH_RE = re.compile(r"^(?:~?/|/)[^\s]+(?:\s+[^\s]+\.(?:md|txt|json))?\s+", re.I)
NOISY_PREFIXES = (
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<command-name>",
    "<system-reminder>",
    "<environment_context>",
    "<recommended_plugins>",
    "# AGENTS.md instructions",
)
GENERIC_TITLES = {"hi", "hello", "hey", "test", "ok", "codex", "claude", "你好", "在吗"}


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def visible_text(content: Any, allowed_types: set[str] | None = None) -> str:
    """Extract visible prose while excluding thinking, tools, and binary content."""
    allowed = allowed_types or {"text", "input_text", "output_text"}
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    chunks: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in allowed:
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            chunks.append(text.strip())
    return "\n\n".join(chunks)


def clean_title(text: str, limit: int = 88) -> str:
    text = ANSI_RE.sub("", text).strip()
    text = LEADING_PATH_RE.sub("", text)
    if "接着这个聊：" in text:
        text = text.split("接着这个聊：", 1)[1]
    lines = [SPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    meaningful = [line for line in lines if line and not line.startswith(NOISY_PREFIXES)]
    title = " ".join(meaningful) if meaningful else "Untitled session"
    title = re.sub(r"^[#>*`\-\s]+", "", title)
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def is_conversation_text(text: str) -> bool:
    return bool(text.strip()) and not text.lstrip().startswith(NOISY_PREFIXES)


def choose_title(messages: Iterable[Message], explicit: str | None = None) -> str:
    if explicit and explicit.strip():
        return clean_title(explicit)
    candidates = [message.text for message in messages if message.role == "user"]
    for candidate in candidates:
        if candidate.lstrip().startswith(NOISY_PREFIXES):
            continue
        title = clean_title(candidate)
        if title.lower() not in GENERIC_TITLES and len(title) >= 8:
            return title
    for candidate in candidates:
        if not candidate.lstrip().startswith(NOISY_PREFIXES):
            return clean_title(candidate)
    return clean_title(candidates[0]) if candidates else "Untitled session"


def iso_from_mtime(path: Path) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


class SourceAdapter(ABC):
    name: str

    @abstractmethod
    def discover(self) -> Iterable[Path]:
        raise NotImplementedError

    @abstractmethod
    def parse(self, path: Path) -> Session | None:
        raise NotImplementedError
