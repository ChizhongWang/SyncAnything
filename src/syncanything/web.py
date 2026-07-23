from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlparse

from syncanything.index import ConversationIndex
from syncanything.service import SyncAnythingService


class SyncAnythingHandler(BaseHTTPRequestHandler):
    index: ConversationIndex
    service: SyncAnythingService

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self._json(self.index.stats())
            return
        if parsed.path == "/api/sessions":
            query = parse_qs(parsed.query)
            phrase = query.get("q", [""])[0]
            source = query.get("source", [None])[0] or None
            limit = int(query.get("limit", ["50"])[0])
            results = self.service.search_sessions(phrase, source=source, limit=limit)
            self._json({"results": results})
            return
        if parsed.path == "/api/session":
            query = parse_qs(parsed.query)
            session_id = query.get("id", [""])[0]
            session = self.service.get_session(session_id, max_chars=100_000)
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
        if urlparse(self.path).path != "/api/reindex":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        report = self.index.index_all()
        self._json(report)

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
