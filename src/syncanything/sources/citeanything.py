from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from syncanything.models import Message, Session
from syncanything.sources.base import SourceAdapter, choose_title, is_conversation_text, visible_text


class CiteAnythingAdapter(SourceAdapter):
    """Read CiteAnything as a product-level source, independent of its agent runtime."""

    name = "citeanything"

    def __init__(
        self,
        cache_root: Path | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("CITEANYTHING_BASE_URL")
            or "https://citeanything.veri-glow.com"
        ).rstrip("/")
        self.api_key = (
            api_key
            if api_key is not None
            else os.environ.get("SYNCANYTHING_CITEANYTHING_API_KEY", "")
        )
        server_key = hashlib.sha256(self.base_url.encode("utf-8")).hexdigest()[:12]
        self.cache_root = (
            cache_root
            or Path(os.environ.get("SYNCANYTHING_HOME", Path.home() / ".syncanything"))
            / "connectors"
            / "citeanything"
            / server_key
        )
        self.sync_error: str | None = None

    def discover(self) -> Iterable[Path]:
        if self.api_key:
            self._sync_remote()
        if not self.cache_root.exists():
            return []
        return sorted(self.cache_root.glob("conversation-*.json"))

    def _sync_remote(self) -> None:
        self.sync_error = None
        try:
            offset = 0
            seen: set[str] = set()
            while True:
                query = urlencode({"limit": 100, "offset": offset})
                payload = self._request_json(f"/api/conversations?{query}")
                conversations = payload.get("conversations", []) if isinstance(payload, dict) else []
                if not isinstance(conversations, list) or not conversations:
                    break

                new_count = 0
                for summary in conversations:
                    if not isinstance(summary, dict) or summary.get("id") is None:
                        continue
                    conversation_id = str(summary["id"])
                    if conversation_id in seen:
                        continue
                    seen.add(conversation_id)
                    new_count += 1
                    detail = self._request_json(f"/api/conversations/{conversation_id}")
                    if not isinstance(detail, dict):
                        continue
                    snapshot = {**summary, **detail, "_syncanything_base_url": self.base_url}
                    self._write_snapshot(conversation_id, snapshot)

                if new_count == 0 or len(conversations) < 100:
                    break
                offset += new_count
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
            self.sync_error = f"{type(error).__name__}: {error}"

    def _request_json(self, path: str) -> Any:
        request = Request(
            f"{self.base_url}{path}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "SyncAnything/0.1",
            },
        )
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def _write_snapshot(self, conversation_id: str, snapshot: dict[str, Any]) -> None:
        self.cache_root.mkdir(parents=True, exist_ok=True)
        path = self.cache_root / f"conversation-{conversation_id}.json"
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") == encoded:
            return
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(path)

    def parse(self, path: Path) -> Session | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("id") is None:
            return None

        messages: list[Message] = []
        for event in payload.get("events", []):
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if event_type in {"user", "assistant"}:
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                role = message.get("role") or event_type
                if role not in {"user", "assistant"}:
                    continue
                text = visible_text(message.get("content"))
            elif event_type == "result":
                role = "assistant"
                text = event.get("result") if isinstance(event.get("result"), str) else ""
            else:
                continue
            if not is_conversation_text(text):
                continue
            if messages and messages[-1].role == role and messages[-1].text == text:
                continue
            messages.append(Message(role=role, text=text))

        if not messages:
            return None
        if not any(message.role == "user" for message in messages):
            title = payload.get("title")
            if isinstance(title, str) and is_conversation_text(title):
                messages.insert(0, Message(role="user", text=title))
        conversation_id = str(payload["id"])
        base_url = str(payload.get("_syncanything_base_url") or self.base_url).rstrip("/")
        runtime = payload.get("runtime")
        if not isinstance(runtime, str) or not runtime:
            runtime = "claude-code"
        return Session(
            source=self.name,
            native_id=conversation_id,
            path=path,
            messages=messages,
            title=choose_title(messages, payload.get("title")),
            started_at=payload.get("created_at") if isinstance(payload.get("created_at"), str) else None,
            updated_at=payload.get("updated_at") if isinstance(payload.get("updated_at"), str) else None,
            metadata={
                "runtime": runtime,
                "execution_session_id": str(payload.get("session_id") or ""),
                "canonical_url": f"{base_url}/chat?conversation_id={conversation_id}",
                "connection": "citeanything-api",
            },
        )
