from __future__ import annotations

import json
import sqlite3
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError, URLError

from syncanything.connections import ConnectionStore, SITE_URLS
from syncanything.index import ConversationIndex
from syncanything.service import SyncAnythingService
from syncanything.sources.citeanything import CiteAnythingAdapter


class SyncAnythingHandler(BaseHTTPRequestHandler):
    index: ConversationIndex
    service: SyncAnythingService
    sync_lock = threading.Lock()
    sync_state: dict[str, object] = {"running": False, "report": None, "error": None}

    def _refresh_local(self) -> None:
        """Re-scan local sources before serving a read.

        Skipped while a full sync runs: that background thread owns its own
        connection, and a second writer would contend for the database.
        """
        if self.sync_state.get("running"):
            return
        try:
            self.index.refresh()
        except sqlite3.Error:
            pass  # A stale answer beats failing the request.

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self._refresh_local()
            self._json(self.index.stats())
            return
        if parsed.path == "/api/connections":
            self._json({"citeanything": ConnectionStore().public_connections()})
            return
        if parsed.path == "/api/sync":
            self._json(dict(self.sync_state))
            return
        if parsed.path == "/api/sessions":
            self._refresh_local()
            query = parse_qs(parsed.query)
            phrase = query.get("q", [""])[0]
            source = query.get("source", [None])[0] or None
            limit = int(query.get("limit", ["50"])[0])
            results = self.service.search_sessions(phrase, source=source, limit=limit)
            total = self.service.count_sessions(phrase, source=source)
            self._json({"results": results, "total": total})
            return
        if parsed.path == "/api/session":
            query = parse_qs(parsed.query)
            session_id = query.get("id", [""])[0]
            focus_value = query.get("focus_ordinal", [""])[0]
            try:
                focus_ordinal = int(focus_value) if focus_value != "" else None
            except ValueError:
                focus_ordinal = None
            session = self.service.get_session(
                session_id,
                max_chars=100_000,
                focus_ordinal=focus_ordinal,
                focus_query=query.get("focus_query", [""])[0],
            )
            if session is None:
                self._json({"error": "Session not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._json(session)
            return
        if parsed.path in {"/", "/index.html"}:
            self._static("index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/styles.css":
            self._static("styles.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self._static("app.js", "text/javascript; charset=utf-8")
            return
        if parsed.path == "/logo.svg":
            self._static("logo.svg", "image/svg+xml")
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/connections/citeanything":
            self._add_citeanything_connection()
            return
        if path != "/api/reindex":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        started = self._begin_reindex()
        self._json(
            {"started": started, **dict(self.sync_state)},
            HTTPStatus.ACCEPTED,
        )

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        prefix = "/api/connections/citeanything/"
        if not path.startswith(prefix):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        connection_id = path[len(prefix) :]
        removed = ConnectionStore().remove_citeanything(connection_id)
        self._json({"removed": removed}, HTTPStatus.OK if removed else HTTPStatus.NOT_FOUND)

    def _add_citeanything_connection(self) -> None:
        # Failures carry a stable `code` so the browser can render them in the
        # reader's language; `error` stays as an English fallback for API clients.
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 32_768:
                raise ValueError("Invalid request body")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            site = str(payload.get("site") or "custom")
            base_url = str(payload.get("base_url") or SITE_URLS.get(site, "")).rstrip("/")
            default_name = "CiteAnything China" if site == "china" else "CiteAnything International"
            name = str(payload.get("name") or default_name)
            api_key = str(payload.get("api_key") or "").strip()
            CiteAnythingAdapter(connections=[]).validate(base_url, api_key)
            connection = ConnectionStore().add_citeanything(name, base_url, api_key, site)
            started = self._begin_reindex()
            self._json(
                {
                    "connection": connection.public_dict(True),
                    "sync_started": started,
                },
                HTTPStatus.CREATED,
            )
        except HTTPError as error:
            self._json(
                {
                    "code": "key_rejected",
                    "status": error.code,
                    "error": f"CiteAnything rejected the key (HTTP {error.code})",
                },
                HTTPStatus.UNAUTHORIZED,
            )
        except (URLError, TimeoutError, OSError) as error:
            self._json(
                {
                    "code": "unreachable",
                    "detail": str(error),
                    "error": f"Could not reach CiteAnything: {error}",
                },
                HTTPStatus.BAD_GATEWAY,
            )
        except (ValueError, RuntimeError, json.JSONDecodeError) as error:
            self._json(
                {"code": "invalid_request", "detail": str(error), "error": str(error)},
                HTTPStatus.BAD_REQUEST,
            )

    def _begin_reindex(self) -> bool:
        if not self.sync_lock.acquire(blocking=False):
            return False
        type(self).sync_state = {"running": True, "report": None, "error": None}

        def run() -> None:
            try:
                with ConversationIndex(self.index.db_path) as background_index:
                    report = background_index.index_all()
                type(self).sync_state = {
                    "running": False,
                    "report": report,
                    "error": None,
                }
            except Exception as error:
                type(self).sync_state = {
                    "running": False,
                    "report": None,
                    "error": f"{type(error).__name__}: {error}",
                }
            finally:
                self.sync_lock.release()

        threading.Thread(target=run, name="syncanything-index", daemon=True).start()
        return True

    def _static(self, name: str, content_type: str) -> None:
        data = files("syncanything.static").joinpath(name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


def serve(index: ConversationIndex, host: str = "127.0.0.1", port: int = 7331) -> None:
    handler = type(
        "BoundSyncAnythingHandler",
        (SyncAnythingHandler,),
        {"index": index, "service": SyncAnythingService(index)},
    )
    server = HTTPServer((host, port), handler)
    print(f"SyncAnything is running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
