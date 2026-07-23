from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from syncanything.connections import CiteAnythingConnection, ConnectionStore, syncanything_home
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
        connections: list[tuple[CiteAnythingConnection, str]] | None = None,
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
        self.cache_root = cache_root or syncanything_home() / "connectors" / "citeanything"
        if connections is not None:
            self.connections = connections
        else:
            self.connections = self._configured_connections()
        self.sync_error: str | None = None
        self.sync_errors: list[dict[str, str]] = []

    def discover(self) -> Iterable[Path]:
        for connection, secret in self.connections:
            if secret:
                self._sync_remote(connection, secret)
        if not self.cache_root.exists():
            return []
        return sorted(self.cache_root.glob("**/conversation-*.json"))

    def _configured_connections(self) -> list[tuple[CiteAnythingConnection, str]]:
        configured: list[tuple[CiteAnythingConnection, str]] = []
        store = ConnectionStore()
        for connection in store.list_citeanything():
            configured.append((connection, store.get_secret(connection.id)))
        if self.api_key:
            connection_id = self._connection_slug(self.base_url)
            if not any(item.id == connection_id for item, _ in configured):
                configured.append(
                    (
                        CiteAnythingConnection(
                            id=connection_id,
                            name="CiteAnything 环境变量连接",
                            base_url=self.base_url,
                        ),
                        self.api_key,
                    )
                )
        return configured

    @staticmethod
    def _connection_slug(base_url: str) -> str:
        host = base_url.split("://", 1)[-1].split("/", 1)[0].lower()
        if host == "citeanything.cn":
            return "china"
        if host == "citeanything.veri-glow.com":
            return "international"
        return "".join(
            character if character.isalnum() else "-" for character in host
        ).strip("-")

    def _connection_cache(self, connection_id: str) -> Path:
        digest = hashlib.sha256(connection_id.encode("utf-8")).hexdigest()[:12]
        return self.cache_root / digest

    def validate(self, base_url: str, api_key: str) -> None:
        self._request_json(
            base_url.rstrip("/"), api_key.strip(), "/api/conversations?limit=1&offset=0"
        )

    def _sync_remote(self, connection: CiteAnythingConnection, api_key: str) -> None:
        self.sync_error = None
        try:
            offset = 0
            seen: set[str] = set()
            while True:
                query = urlencode({"limit": 100, "offset": offset})
                payload = self._request_json(
                    connection.base_url, api_key, f"/api/conversations?{query}"
                )
                conversations = payload.get("conversations", []) if isinstance(payload, dict) else []
                if not isinstance(conversations, list) or not conversations:
                    break

                pending: dict[Any, tuple[str, dict[str, Any]]] = {}
                for summary in conversations:
                    if not isinstance(summary, dict) or summary.get("id") is None:
                        continue
                    conversation_id = str(summary["id"])
                    if conversation_id in seen:
                        continue
                    seen.add(conversation_id)
                    pending[conversation_id] = (conversation_id, summary)

                with ThreadPoolExecutor(max_workers=6) as executor:
                    futures = {
                        executor.submit(
                            self._request_json,
                            connection.base_url,
                            api_key,
                            f"/api/conversations/{conversation_id}",
                        ): (conversation_id, summary)
                        for conversation_id, summary in pending.values()
                    }
                    for future in as_completed(futures):
                        conversation_id, summary = futures[future]
                        try:
                            detail = future.result()
                        except (
                            HTTPError,
                            URLError,
                            TimeoutError,
                            OSError,
                            ValueError,
                            json.JSONDecodeError,
                        ) as error:
                            message = f"{type(error).__name__}: {error}"
                            self.sync_error = message
                            self.sync_errors.append(
                                {
                                    "connection_id": connection.id,
                                    "conversation_id": conversation_id,
                                    "error": message,
                                }
                            )
                            continue
                        if not isinstance(detail, dict):
                            continue
                        snapshot = {
                            **summary,
                            **detail,
                            "_syncanything_base_url": connection.base_url,
                            "_syncanything_connection_id": connection.id,
                            "_syncanything_connection_name": connection.name,
                        }
                        self._write_snapshot(connection.id, conversation_id, snapshot)

                new_count = len(pending)
                if new_count == 0 or len(conversations) < 100:
                    break
                offset += new_count
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
            message = f"{type(error).__name__}: {error}"
            self.sync_errors.append({"connection_id": connection.id, "error": message})
            self.sync_error = message

    def _request_json(self, base_url: str, api_key: str, path: str) -> Any:
        request = Request(
            f"{base_url}{path}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "SyncAnything/0.1",
            },
        )
        with urlopen(request, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))

    def _write_snapshot(
        self, connection_id: str, conversation_id: str, snapshot: dict[str, Any]
    ) -> None:
        cache = self._connection_cache(connection_id)
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / f"conversation-{conversation_id}.json"
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
        connection_id = str(
            payload.get("_syncanything_connection_id") or self._connection_slug(base_url)
        )
        connection_name = str(
            payload.get("_syncanything_connection_name") or connection_id
        )
        runtime = payload.get("runtime")
        if not isinstance(runtime, str) or not runtime:
            runtime = "claude-code"
        return Session(
            source=self.name,
            native_id=f"{connection_id}:{conversation_id}",
            path=path,
            messages=messages,
            title=choose_title(messages, payload.get("title")),
            started_at=payload.get("created_at") if isinstance(payload.get("created_at"), str) else None,
            updated_at=payload.get("updated_at") if isinstance(payload.get("updated_at"), str) else None,
            metadata={
                "runtime": runtime,
                "execution_session_id": str(payload.get("session_id") or ""),
                "canonical_url": f"{base_url}/chat?conversation_id={conversation_id}",
                "connection": connection_id,
                "connection_name": connection_name,
                "base_url": base_url,
            },
        )
