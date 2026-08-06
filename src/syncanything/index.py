from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from syncanything.connections import syncanything_home
from syncanything.metrics import TextMetrics, book_equivalents, measure
from syncanything.models import Message, Session
from syncanything.sources import SourceAdapter, default_adapters, local_adapters

# Denormalised onto `sessions` so status reads stay a single aggregate query
# instead of re-measuring every message.
TEXT_METRIC_COLUMNS = ("char_count", "word_count", "token_estimate", "text_bytes")


def default_db_path() -> Path:
    explicit = os.environ.get("SYNCANYTHING_DB")
    if explicit:
        return Path(explicit).expanduser()
    return syncanything_home() / "index.db"


def file_fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


class ConversationIndex:
    def __init__(
        self,
        db_path: Path | None = None,
        adapters: Iterable[SourceAdapter] | None = None,
    ) -> None:
        # The index owns the source set it represents, so refresh() re-scans exactly
        # those sources instead of assuming the default home directories.
        self.adapters: list[SourceAdapter] | None = list(adapters) if adapters is not None else None
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
                char_count INTEGER NOT NULL DEFAULT 0,
                word_count INTEGER NOT NULL DEFAULT 0,
                token_estimate INTEGER NOT NULL DEFAULT 0,
                text_bytes INTEGER NOT NULL DEFAULT 0,
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

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self.connection.commit()
        self._add_text_metric_columns()

    def _add_text_metric_columns(self) -> None:
        """Bring an index built before text metrics existed up to date.

        The columns are added empty and backfilled from the messages already
        indexed, so an existing database gains the numbers without a reindex.
        """
        existing = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(sessions)")
        }
        missing = [column for column in TEXT_METRIC_COLUMNS if column not in existing]
        if not missing:
            return
        with self.connection:
            for column in missing:
                self.connection.execute(
                    f"ALTER TABLE sessions ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                )
        self._backfill_text_metrics()

    def _backfill_text_metrics(self) -> None:
        totals: dict[str, TextMetrics] = {}
        cursor = self.connection.execute("SELECT session_id, text FROM messages")
        for row in cursor:
            session_id = row["session_id"]
            totals[session_id] = totals.get(session_id, TextMetrics()) + measure(row["text"])
        if not totals:
            return
        with self.connection:
            self.connection.executemany(
                "UPDATE sessions SET char_count = ?, word_count = ?, "
                "token_estimate = ?, text_bytes = ? WHERE id = ?",
                [
                    (metrics.characters, metrics.words, metrics.tokens, metrics.bytes, session_id)
                    for session_id, metrics in totals.items()
                ],
            )

    def _get_meta(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.connection.commit()

    def last_refresh(self, scope: str) -> float:
        raw = self._get_meta(f"last_refresh:{scope}")
        try:
            return float(raw) if raw else 0.0
        except ValueError:
            return 0.0

    def refresh(self, max_age_seconds: float = 2.0, force: bool = False) -> dict[str, Any] | None:
        """Incrementally re-index local sources before a read.

        Scanning every local session file costs about 15ms, so this runs inline on
        search, list, show, reference, and status. Remote sources are excluded: their
        discover() performs HTTP requests and belongs to explicit `index` or `serve`.
        Returns None when a recent refresh made this call unnecessary.
        """
        if not force and (time.time() - self.last_refresh("local")) < max_age_seconds:
            return None
        sources = self.adapters if self.adapters is not None else local_adapters()
        local = [adapter for adapter in sources if not adapter.is_remote]
        if not local:
            return None
        report = self.index_all(adapters=local)
        self._set_meta("last_refresh:local", str(time.time()))
        return report

    def index_all(
        self,
        adapters: Iterable[SourceAdapter] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        report: dict[str, Any] = {
            "database": str(self.db_path),
            "indexed": 0,
            "unchanged": 0,
            "empty": 0,
            "skipped": 0,
            "removed": 0,
            "errors": [],
            "sources": {},
        }
        for adapter in adapters or self.adapters or default_adapters():
            discovered = list(adapter.discover())
            source_report = {
                "discovered": len(discovered),
                "indexed": 0,
                "unchanged": 0,
                "empty": 0,
                "skipped": 0,
                "errors": 0,
            }
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
                        report["unchanged"] += 1
                        report["skipped"] += 1
                        source_report["unchanged"] += 1
                        source_report["skipped"] += 1
                        continue
                    session = adapter.parse(path)
                    if session is None:
                        # Parsed to nothing visible (e.g. a tool-only transcript).
                        report["removed"] += self._delete_path(str(path))
                        report["empty"] += 1
                        report["skipped"] += 1
                        source_report["empty"] += 1
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
        metrics = TextMetrics()
        for message in session.messages:
            metrics = metrics + measure(message.text)
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
                    source_path, message_count, char_count, word_count, token_estimate,
                    text_bytes, fingerprint, metadata_json, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    cwd=excluded.cwd,
                    started_at=excluded.started_at,
                    updated_at=excluded.updated_at,
                    source_path=excluded.source_path,
                    message_count=excluded.message_count,
                    char_count=excluded.char_count,
                    word_count=excluded.word_count,
                    token_estimate=excluded.token_estimate,
                    text_bytes=excluded.text_bytes,
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
                    metrics.characters,
                    metrics.words,
                    metrics.tokens,
                    metrics.bytes,
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
                   (
                     SELECT MIN(m.ordinal)
                     FROM messages m
                     WHERE m.session_id = s.id
                       AND m.text = messages_fts.text
                   ) AS match_ordinal,
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
                   s.source_path, s.message_count, s.metadata_json,
                   m.ordinal AS match_ordinal, m.text AS snippet, 0 AS rank
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

    def storage_bytes(self) -> int:
        """Disk footprint of the index, including the write-ahead log.

        The trigram FTS index is several times the size of the text it covers,
        so this is materially larger than the `text_bytes` it is built from.
        """
        total = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = self.db_path.with_name(self.db_path.name + suffix)
            try:
                total += candidate.stat().st_size
            except OSError:
                continue
        return total

    def stats(self) -> dict[str, Any]:
        rows = self.connection.execute(
            """
            SELECT source,
                   COUNT(*) AS sessions,
                   SUM(message_count) AS messages,
                   SUM(char_count) AS characters,
                   SUM(word_count) AS words,
                   SUM(token_estimate) AS tokens,
                   SUM(text_bytes) AS text_bytes
            FROM sessions GROUP BY source
            """
        ).fetchall()
        sources = [
            {key: (value or 0) if key != "source" else value for key, value in dict(row).items()}
            for row in rows
        ]
        totals = {
            key: sum(source[key] for source in sources)
            for key in ("sessions", "messages", "characters", "words", "tokens", "text_bytes")
        }
        return {
            "database": str(self.db_path),
            "sources": sources,
            **totals,
            "storage_bytes": self.storage_bytes(),
            "books": book_equivalents(totals["characters"], totals["words"]),
        }
