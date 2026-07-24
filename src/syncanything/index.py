from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from syncanything.models import Message, Session
from syncanything.sources import SourceAdapter, default_adapters


def default_db_path() -> Path:
    explicit = os.environ.get("SYNCANYTHING_DB")
    if explicit:
        return Path(explicit).expanduser()
    home = Path(os.environ.get("SYNCANYTHING_HOME", Path.home() / ".syncanything"))
    return home.expanduser() / "index.db"


def file_fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


class ConversationIndex:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ConversationIndex":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                native_id TEXT NOT NULL,
                title TEXT NOT NULL,
                cwd TEXT,
                started_at TEXT,
                updated_at TEXT,
                source_path TEXT NOT NULL UNIQUE,
                message_count INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS sessions_source_updated
            ON sessions(source, updated_at DESC);

            CREATE TABLE IF NOT EXISTS messages (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                role TEXT NOT NULL,
                timestamp TEXT,
                text TEXT NOT NULL,
                PRIMARY KEY(session_id, ordinal)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                session_id UNINDEXED,
                text,
                tokenize='trigram'
            );
            """
        )
        self.connection.commit()

    def index_all(
        self,
        adapters: Iterable[SourceAdapter] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        report: dict[str, Any] = {
            "database": str(self.db_path),
            "indexed": 0,
            "skipped": 0,
            "removed": 0,
            "errors": [],
            "sources": {},
        }
        for adapter in adapters or default_adapters():
            discovered = list(adapter.discover())
            source_report = {"discovered": len(discovered), "indexed": 0, "skipped": 0, "errors": 0}
            sync_error = getattr(adapter, "sync_error", None)
            if sync_error:
                source_report["sync_error"] = sync_error
            report["sources"][adapter.name] = source_report
            for path in discovered:
                try:
                    fingerprint = file_fingerprint(path)
                    existing = self.connection.execute(
                        "SELECT fingerprint FROM sessions WHERE source_path = ?", (str(path),)
                    ).fetchone()
                    if existing and existing["fingerprint"] == fingerprint and not force:
                        report["skipped"] += 1
                        source_report["skipped"] += 1
                        continue
                    session = adapter.parse(path)
                    if session is None:
                        report["removed"] += self._delete_path(str(path))
                        report["skipped"] += 1
                        source_report["skipped"] += 1
                        continue
                    self.put_session(session, fingerprint)
                    report["indexed"] += 1
                    source_report["indexed"] += 1
                except (OSError, sqlite3.Error, ValueError) as error:
                    report["errors"].append({"path": str(path), "error": str(error)})
                    source_report["errors"] += 1
            discovered_paths = {str(path) for path in discovered}
            indexed_paths = self.connection.execute(
                "SELECT source_path FROM sessions WHERE source = ?", (adapter.name,)
            ).fetchall()
            for row in indexed_paths:
                if row["source_path"] not in discovered_paths:
                    report["removed"] += self._delete_path(row["source_path"])
        return report

    def put_session(self, session: Session, fingerprint: str) -> None:
        with self.connection:
            old = self.connection.execute(
                "SELECT id FROM sessions WHERE source_path = ?", (str(session.path),)
            ).fetchone()
            if old and old["id"] != session.id:
                self._delete_session(old["id"])
            self.connection.execute(
                """
                INSERT INTO sessions (
                    id, source, native_id, title, cwd, started_at, updated_at,
                    source_path, message_count, fingerprint, metadata_json, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    cwd=excluded.cwd,
                    started_at=excluded.started_at,
                    updated_at=excluded.updated_at,
                    source_path=excluded.source_path,
                    message_count=excluded.message_count,
                    fingerprint=excluded.fingerprint,
                    metadata_json=excluded.metadata_json,
                    indexed_at=CURRENT_TIMESTAMP
                """,
                (
                    session.id,
                    session.source,
                    session.native_id,
                    session.title,
                    session.cwd,
                    session.started_at,
                    session.updated_at,
                    str(session.path),
                    len(session.messages),
                    fingerprint,
                    json.dumps(session.metadata, ensure_ascii=False),
                ),
            )
            self.connection.execute("DELETE FROM messages WHERE session_id = ?", (session.id,))
            self.connection.execute("DELETE FROM messages_fts WHERE session_id = ?", (session.id,))
            self.connection.executemany(
                "INSERT INTO messages(session_id, ordinal, role, timestamp, text) VALUES (?, ?, ?, ?, ?)",
                [
                    (session.id, ordinal, message.role, message.timestamp, message.text)
                    for ordinal, message in enumerate(session.messages)
                ],
            )
            self.connection.executemany(
                "INSERT INTO messages_fts(session_id, text) VALUES (?, ?)",
                [(session.id, message.text) for message in session.messages],
            )

    def _delete_session(self, session_id: str) -> None:
        self.connection.execute("DELETE FROM messages_fts WHERE session_id = ?", (session_id,))
        self.connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def _delete_path(self, source_path: str) -> int:
        row = self.connection.execute(
            "SELECT id FROM sessions WHERE source_path = ?", (source_path,)
        ).fetchone()
        if row is None:
            return 0
        with self.connection:
            self._delete_session(row["id"])
        return 1

    def list_sessions(self, source: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if source:
            clauses.append("source = ?")
            params.append(source)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 500)))
        rows = self.connection.execute(
            f"""
            SELECT id, source, native_id, title, cwd, started_at, updated_at,
                   source_path, message_count, metadata_json
            FROM sessions {where}
            ORDER BY COALESCE(updated_at, indexed_at) DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [self._present_result(row) for row in rows]

    def count_sessions(self, source: str | None = None) -> int:
        if source:
            row = self.connection.execute(
                "SELECT COUNT(*) AS count FROM sessions WHERE source = ?", (source,)
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT COUNT(*) AS count FROM sessions"
            ).fetchone()
        return int(row["count"] if row else 0)

    def search(
        self,
        query: str,
        source: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return self.list_sessions(source=source, limit=limit)
        limit = max(1, min(limit, 100))
        if len(query) >= 3:
            try:
                return self._search_fts(query, source, limit)
            except sqlite3.OperationalError:
                pass
        return self._search_like(query, source, limit)

    def count_search(self, query: str, source: str | None = None) -> int:
        query = query.strip()
        if not query:
            return self.count_sessions(source=source)
        if len(query) >= 3:
            try:
                return self._count_search_fts(query, source)
            except sqlite3.OperationalError:
                pass
        return self._count_search_like(query, source)

    def _search_fts(self, query: str, source: str | None, limit: int) -> list[dict[str, Any]]:
        phrase = '"' + query.replace('"', '""') + '"'
        source_clause = "AND s.source = ?" if source else ""
        params: list[Any] = [phrase]
        if source:
            params.append(source)
        params.append(limit * 8)
        rows = self.connection.execute(
            f"""
            SELECT s.id, s.source, s.native_id, s.title, s.cwd, s.updated_at,
                   s.source_path, s.message_count, s.metadata_json,
                   snippet(messages_fts, 1, '<mark>', '</mark>', '…', 28) AS snippet,
                   bm25(messages_fts) AS rank
            FROM messages_fts
            JOIN sessions s ON s.id = messages_fts.session_id
            WHERE messages_fts MATCH ? {source_clause}
            ORDER BY rank, s.updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return self._deduplicate_results(rows, limit)

    def _search_like(self, query: str, source: str | None, limit: int) -> list[dict[str, Any]]:
        source_clause = "AND s.source = ?" if source else ""
        params: list[Any] = [f"%{query}%"]
        if source:
            params.append(source)
        params.append(limit * 8)
        rows = self.connection.execute(
            f"""
            SELECT s.id, s.source, s.native_id, s.title, s.cwd, s.updated_at,
                   s.source_path, s.message_count, s.metadata_json, m.text AS snippet, 0 AS rank
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE m.text LIKE ? {source_clause}
            ORDER BY s.updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        results = self._deduplicate_results(rows, limit)
        for result in results:
            result["snippet"] = self._plain_snippet(result["snippet"], query)
        return results

    def _count_search_fts(self, query: str, source: str | None) -> int:
        phrase = '"' + query.replace('"', '""') + '"'
        source_clause = "AND s.source = ?" if source else ""
        params: list[Any] = [phrase]
        if source:
            params.append(source)
        row = self.connection.execute(
            f"""
            SELECT COUNT(DISTINCT s.id) AS count
            FROM messages_fts
            JOIN sessions s ON s.id = messages_fts.session_id
            WHERE messages_fts MATCH ? {source_clause}
            """,
            params,
        ).fetchone()
        return int(row["count"] if row else 0)

    def _count_search_like(self, query: str, source: str | None) -> int:
        source_clause = "AND s.source = ?" if source else ""
        params: list[Any] = [f"%{query}%"]
        if source:
            params.append(source)
        row = self.connection.execute(
            f"""
            SELECT COUNT(DISTINCT s.id) AS count
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE m.text LIKE ? {source_clause}
            """,
            params,
        ).fetchone()
        return int(row["count"] if row else 0)

    @staticmethod
    def _deduplicate_results(rows: Iterable[sqlite3.Row], limit: int) -> list[dict[str, Any]]:
        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        for row in rows:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            result = ConversationIndex._present_result(row)
            result["uri"] = f"syncanything://session/{result['id']}"
            results.append(result)
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _present_result(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        metadata_raw = result.pop("metadata_json", "{}")
        try:
            metadata = json.loads(metadata_raw or "{}")
        except json.JSONDecodeError:
            metadata = {}
        canonical_url = metadata.get("canonical_url")
        if isinstance(canonical_url, str) and canonical_url:
            result["canonical_url"] = canonical_url
        return result

    @staticmethod
    def _plain_snippet(text: str, query: str, radius: int = 110) -> str:
        position = text.lower().find(query.lower())
        if position < 0:
            return text[: radius * 2]
        start = max(0, position - radius)
        end = min(len(text), position + len(query) + radius)
        prefix = "…" if start else ""
        suffix = "…" if end < len(text) else ""
        matched = text[position : position + len(query)]
        body = text[start:position] + f"<mark>{matched}</mark>" + text[position + len(query) : end]
        return prefix + body + suffix

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT id, source, native_id, title, cwd, started_at, updated_at,
                   source_path, message_count, metadata_json
            FROM sessions WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        session = dict(row)
        session["metadata"] = json.loads(session.pop("metadata_json") or "{}")
        session["uri"] = f"syncanything://session/{session_id}"
        messages = self.connection.execute(
            "SELECT ordinal, role, timestamp, text FROM messages WHERE session_id = ? ORDER BY ordinal",
            (session_id,),
        ).fetchall()
        session["messages"] = [dict(message) for message in messages]
        return session

    def stats(self) -> dict[str, Any]:
        rows = self.connection.execute(
            "SELECT source, COUNT(*) AS sessions, SUM(message_count) AS messages FROM sessions GROUP BY source"
        ).fetchall()
        return {
            "database": str(self.db_path),
            "sources": [dict(row) for row in rows],
            "sessions": sum(row["sessions"] for row in rows),
            "messages": sum(row["messages"] or 0 for row in rows),
        }
