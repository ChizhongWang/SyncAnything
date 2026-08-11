from __future__ import annotations

import json
import os
import platform
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from syncanything import __version__
from syncanything.connections import (
    CiteAnythingConnection,
    ConnectionStore,
    syncanything_home,
)
from syncanything.index import ConversationIndex
from syncanything.mcp import McpServer
from syncanything.metrics import TextMetrics, book_equivalents, measure
from syncanything.service import SyncAnythingService
from syncanything.shortcut import (
    HOTKEY_LABEL,
    SERVER_LABEL,
    _bootstrap_launch_agent,
    hotkey_launch_agent,
    hotkey_source,
    server_launch_agent,
    shortcut_paths,
)
from syncanything.sources.citeanything import CiteAnythingAdapter
from syncanything.sources.claude import ClaudeAdapter
from syncanything.sources.codex import CodexAdapter
from syncanything.sources.cursor import CursorAdapter
from syncanything.sources.kimi import KimiAdapter
from syncanything.sources.pi import PiAdapter
from syncanything.web import SyncAnythingHandler


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n")


class AdapterTests(unittest.TestCase):
    def test_module_entrypoint_reports_package_version(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "syncanything", "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), f"syncanything {__version__}")

    def test_web_static_assets_are_packaged(self) -> None:
        static = files("syncanything.static")
        for name in ("app.js", "index.html", "logo.svg", "styles.css"):
            self.assertTrue(static.joinpath(name).is_file(), name)

    def test_web_assets_include_native_overlay_mode(self) -> None:
        static = files("syncanything.static")
        script = static.joinpath("app.js").read_text(encoding="utf-8")
        styles = static.joinpath("styles.css").read_text(encoding="utf-8")
        self.assertIn('PAGE_PARAMS.get("overlay") === "1"', script)
        self.assertIn("syncAnythingOverlayDidShow", script)
        self.assertIn('type: "resize"', script)
        self.assertIn('params.set("refresh", "background")', script)
        self.assertIn("html.overlay-mode.overlay-has-query", styles)

    def test_web_refresh_mode_rejects_unknown_values(self) -> None:
        self.assertEqual(SyncAnythingHandler._refresh_mode({}), "sync")
        self.assertEqual(
            SyncAnythingHandler._refresh_mode({"refresh": ["background"]}),
            "background",
        )
        self.assertEqual(
            SyncAnythingHandler._refresh_mode({"refresh": ["unexpected"]}),
            "sync",
        )

    def test_macos_hotkey_registers_the_documented_shortcut(self) -> None:
        source = hotkey_source("http://127.0.0.1:7331")
        self.assertIn("RegisterEventHotKey", source)
        self.assertIn("kVK_ANSI_K", source)
        self.assertIn("cmdKey | controlKey", source)
        self.assertIn("SyncAnythingPanel : NSPanel", source)
        self.assertIn("WKWebView", source)
        self.assertIn("NSVisualEffectMaterialHUDWindow", source)
        self.assertIn('stringByAppendingString:@"/?overlay=1"', source)
        self.assertIn('addScriptMessageHandler:self name:@"syncanything"', source)

    def test_shortcut_launch_agents_use_local_server_and_native_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = shortcut_paths(root / "home", root / "LaunchAgents")
            server = server_launch_agent(Path("/usr/local/bin/syncanything"), paths)
            hotkey = hotkey_launch_agent(paths)
            self.assertEqual(server["Label"], SERVER_LABEL)
            self.assertEqual(
                server["ProgramArguments"],
                ["/usr/local/bin/syncanything", "serve", "--no-index"],
            )
            self.assertTrue(server["RunAtLoad"])
            self.assertTrue(server["KeepAlive"])
            self.assertEqual(hotkey["Label"], HOTKEY_LABEL)
            self.assertEqual(hotkey["ProgramArguments"], [str(paths.helper)])
            self.assertEqual(hotkey["LimitLoadToSessionType"], "Aqua")

    @patch("syncanything.shortcut.time.sleep")
    @patch("syncanything.shortcut._launchctl")
    def test_shortcut_install_retries_launchd_bootstrap(self, launchctl, sleep) -> None:
        launchctl.side_effect = [
            subprocess.CompletedProcess(["launchctl"], 5, "", "Input/output error"),
            subprocess.CompletedProcess(["launchctl"], 0, "", ""),
        ]
        _bootstrap_launch_agent("gui/501", Path("/tmp/example.plist"))
        self.assertEqual(launchctl.call_count, 2)
        sleep.assert_called_once_with(0.25)

    def test_syncanything_home_uses_env_without_expanding_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"SYNCANYTHING_HOME": directory}, clear=True):
                self.assertEqual(syncanything_home(), Path(directory))

    def test_cli_db_override_can_imply_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "index.db"
            env = {
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                "PYTHONUTF8": "1",
                "TEMP": directory,
                "TMP": directory,
            }
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "syncanything",
                    "--db",
                    str(db_path),
                    "status",
                    "--json",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["database"], str(db_path))

    def test_citeanything_does_not_reuse_skill_key(self) -> None:
        with patch.dict(
            os.environ,
            {"CITEANYTHING_API_KEY": "ca_skill_only"},
            clear=True,
        ):
            adapter = CiteAnythingAdapter()
        self.assertEqual(adapter.api_key, "")

    def test_citeanything_keeps_product_identity_separate_from_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation-42.json"
            path.write_text(
                json.dumps(
                    {
                        "id": 42,
                        "title": "核实一项市场数据",
                        "session_id": "underlying-claude-session",
                        "created_at": "2026-07-01T00:00:00Z",
                        "updated_at": "2026-07-01T00:01:00Z",
                        "_syncanything_base_url": "https://citeanything.example",
                        "events": [
                            {
                                "type": "user",
                                "message": {
                                    "role": "user",
                                    "content": [{"type": "text", "text": "帮我核实这项数据"}],
                                },
                            },
                            {
                                "type": "assistant",
                                "message": {
                                    "role": "assistant",
                                    "content": [
                                        {"type": "thinking", "thinking": "private"},
                                        {"type": "text", "text": "已找到一手来源。"},
                                        {"type": "tool_use", "name": "citeanything"},
                                    ],
                                },
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            session = CiteAnythingAdapter(
                cache_root=Path(directory),
                base_url="https://citeanything.example",
                api_key="",
                connections=[],
            ).parse(path)
            assert session is not None
            self.assertEqual(
                session.id, "citeanything:citeanything-example:42"
            )
            self.assertEqual(session.metadata["runtime"], "claude-code")
            self.assertEqual(
                session.metadata["execution_session_id"], "underlying-claude-session"
            )
            self.assertEqual(
                [message.text for message in session.messages],
                ["帮我核实这项数据", "已找到一手来源。"],
            )

    def test_citeanything_namespaces_sessions_by_connection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation-42.json"
            path.write_text(
                json.dumps(
                    {
                        "id": 42,
                        "_syncanything_base_url": "https://citeanything.cn",
                        "_syncanything_connection_id": "china-account",
                        "events": [
                            {"type": "user", "message": {"role": "user", "content": "梁文锋"}}
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            adapter = CiteAnythingAdapter(cache_root=Path(directory), connections=[])
            session = adapter.parse(path)
            assert session is not None
            self.assertEqual(session.id, "citeanything:china-account:42")
            self.assertEqual(session.metadata["connection"], "china-account")

    @unittest.skipUnless(platform.system() == "Windows", "Windows DPAPI only")
    def test_windows_secret_store_round_trips_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConnectionStore(Path(directory))
            store.set_secret("china-account", "ca_secret_123")
            self.assertEqual(store.get_secret("china-account"), "ca_secret_123")
            store.delete_secret("china-account")
            self.assertEqual(store.get_secret("china-account"), "")

    @patch("syncanything.connections.platform.system", return_value="Windows")
    @patch(
        "syncanything.connections._unprotect_windows_secret",
        return_value="ca_secret_123",
    )
    @patch(
        "syncanything.connections._protect_windows_secret",
        return_value="encrypted-by-dpapi",
    )
    def test_windows_secret_store_uses_separate_encrypted_file(
        self,
        protect,
        unprotect,
        system,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConnectionStore(Path(directory))
            store.set_secret("china/account", "ca_secret_123")

            secret_path = (
                Path(directory) / "secrets" / "citeanything-china_account.dpapi"
            )
            self.assertEqual(secret_path.read_text().strip(), "encrypted-by-dpapi")
            self.assertEqual(store.get_secret("china/account"), "ca_secret_123")
            protect.assert_called_once_with("ca_secret_123")
            unprotect.assert_called_once_with("encrypted-by-dpapi")

            store.delete_secret("china/account")
            self.assertFalse(secret_path.exists())

    def test_claude_visible_conversation_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project" / "session-1.jsonl"
            write_jsonl(
                path,
                [
                    {
                        "type": "user",
                        "sessionId": "c1",
                        "timestamp": "2026-07-01T00:00:00Z",
                        "cwd": "/work",
                        "message": {"role": "user", "content": "怎样统一用户语境？"},
                    },
                    {
                        "type": "assistant",
                        "sessionId": "c1",
                        "timestamp": "2026-07-01T00:00:01Z",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "thinking", "thinking": "private"},
                                {"type": "text", "text": "建立只读索引。"},
                                {"type": "tool_use", "name": "bash", "input": {}},
                            ],
                        },
                    },
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [{"type": "tool_result", "content": "secret output"}],
                        },
                    },
                ],
            )
            session = ClaudeAdapter(Path(directory)).parse(path)
            self.assertIsNotNone(session)
            assert session is not None
            self.assertEqual(session.id, "claude:c1")
            self.assertEqual([message.text for message in session.messages], ["怎样统一用户语境？", "建立只读索引。"])

    def test_codex_ignores_developer_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            write_jsonl(
                path,
                [
                    {"type": "session_meta", "timestamp": "2026-07-01T00:00:00Z", "payload": {"id": "x1", "cwd": "/work"}},
                    {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "hidden"}]}},
                    {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "找到之前的讨论"}]}},
                    {"type": "event_msg", "payload": {"type": "user_message", "message": "找到之前的讨论"}},
                    {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "找到了。"}]}},
                ],
            )
            session = CodexAdapter(Path(directory)).parse(path)
            assert session is not None
            self.assertEqual([message.text for message in session.messages], ["找到之前的讨论", "找到了。"])

    def test_kimi_legacy_and_pi(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kimi_path = root / ".kimi" / "sessions" / "project" / "k1" / "context.jsonl"
            write_jsonl(
                kimi_path,
                [
                    {"role": "user", "content": "继续这个任务"},
                    {"role": "assistant", "content": [{"type": "think", "text": "hidden"}, {"type": "text", "text": "可以。"}]},
                    {"role": "tool", "content": "tool output"},
                ],
            )
            kimi = KimiAdapter([root / ".kimi"]).parse(kimi_path)
            assert kimi is not None
            self.assertEqual(len(kimi.messages), 2)

            pi_path = root / ".pi" / "agent" / "sessions" / "--work--" / "p1.jsonl"
            write_jsonl(
                pi_path,
                [
                    {"type": "session", "version": 3, "id": "p1", "timestamp": "2026-07-01T00:00:00Z", "cwd": "/work"},
                    {"type": "message", "id": "1", "parentId": None, "timestamp": "2026-07-01T00:00:01Z", "message": {"role": "user", "content": "读取 Claude 会话"}},
                    {"type": "message", "id": "2", "parentId": "1", "timestamp": "2026-07-01T00:00:02Z", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": "已读取。"}]}},
                ],
            )
            pi = PiAdapter([root / ".pi" / "agent" / "sessions"]).parse(pi_path)
            assert pi is not None
            self.assertEqual(pi.id, "pi:p1")
            self.assertEqual(len(pi.messages), 2)

    def test_cursor_indexes_app_and_cli_without_splitting_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_root = root / "cache"
            chats_root = root / "chats"
            projects_root = root / "projects"
            session_id = "11111111-2222-3333-4444-555555555555"
            composer_id = "aaaa-bbbb-cccc-dddd"
            chat_dir = chats_root / "abcdef0123456789abcdef0123456789" / session_id
            chat_dir.mkdir(parents=True)
            (chat_dir / "meta.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "createdAtMs": 1_700_000_000_000,
                        "updatedAtMs": 1_700_000_100_000,
                        "hasConversation": True,
                        "title": "CLI session about indexing",
                        "cwd": "/work",
                    }
                ),
                encoding="utf-8",
            )
            transcript = (
                projects_root
                / "Users-work"
                / "agent-transcripts"
                / session_id
                / f"{session_id}.jsonl"
            )
            write_jsonl(
                transcript,
                [
                    {
                        "role": "user",
                        "message": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "<timestamp>Sunday, Aug 9, 2026</timestamp>\n"
                                        "<user_query>\n请支持 Cursor CLI 会话索引\n</user_query>"
                                    ),
                                }
                            ]
                        },
                    },
                    {
                        "role": "assistant",
                        "message": {
                            "content": [
                                {"type": "text", "text": "已接入 Cursor CLI。"},
                                {"type": "tool_use", "name": "Shell", "input": {"command": "ls"}},
                            ]
                        },
                    },
                ],
            )

            db_path = root / "state.vscdb"
            connection = sqlite3.connect(db_path)
            connection.execute(
                "CREATE TABLE composerHeaders ("
                "composerId TEXT PRIMARY KEY, lastUpdatedAt INTEGER, isSubagent INTEGER, value TEXT)"
            )
            connection.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
            connection.execute(
                "INSERT INTO composerHeaders(composerId, lastUpdatedAt, isSubagent, value) VALUES (?, ?, 0, ?)",
                (
                    composer_id,
                    1_700_000_050_000,
                    json.dumps({"name": "App composer", "createdAt": 1_700_000_000_000, "unifiedMode": "agent"}),
                ),
            )
            connection.execute(
                "INSERT INTO cursorDiskKV(key, value) VALUES (?, ?)",
                (
                    f"bubbleId:{composer_id}:b1",
                    json.dumps({"type": 1, "text": "App 窗口里的问题", "createdAt": "2026-08-09T00:00:00Z"}),
                ),
            )
            connection.execute(
                "INSERT INTO cursorDiskKV(key, value) VALUES (?, ?)",
                (
                    f"bubbleId:{composer_id}:b2",
                    json.dumps({"type": 2, "text": "App 窗口里的回答", "createdAt": "2026-08-09T00:00:01Z"}),
                ),
            )
            connection.commit()
            connection.close()

            adapter = CursorAdapter(
                cache_root,
                chats_root=chats_root,
                projects_root=projects_root,
                state_dbs=[db_path],
            )
            discovered = list(adapter.discover())
            self.assertEqual(
                {path.name for path in discovered},
                {f"composer-{composer_id}.json", f"cli-{session_id}.json"},
            )

            app_session = adapter.parse(cache_root / f"composer-{composer_id}.json")
            cli_session = adapter.parse(cache_root / f"cli-{session_id}.json")
            assert app_session is not None and cli_session is not None
            self.assertEqual(app_session.id, f"cursor:{composer_id}")
            self.assertEqual(cli_session.id, f"cursor:{session_id}")
            self.assertEqual(app_session.metadata.get("cursor_surface"), "app")
            self.assertEqual(cli_session.metadata.get("cursor_surface"), "cli")
            self.assertEqual(
                [message.text for message in app_session.messages],
                ["App 窗口里的问题", "App 窗口里的回答"],
            )
            self.assertEqual(
                [message.text for message in cli_session.messages],
                ["请支持 Cursor CLI 会话索引", "已接入 Cursor CLI。"],
            )
            self.assertEqual(cli_session.cwd, "/work")


class IndexAndMcpTests(unittest.TestCase):
    def test_count_search_reports_matches_beyond_result_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = root / "sessions"
            for number in range(3):
                write_jsonl(
                    sessions / f"s{number}.jsonl",
                    [
                        {
                            "type": "user",
                            "sessionId": f"s{number}",
                            "message": {
                                "role": "user",
                                "content": f"共同检索词，第 {number + 1} 个会话",
                            },
                        }
                    ],
                )

            with ConversationIndex(root / "index.db") as index:
                report = index.index_all([ClaudeAdapter(sessions)])
                self.assertEqual(report["indexed"], 3)
                self.assertEqual(len(index.search("共同检索词", limit=1)), 1)
                self.assertEqual(index.count_search("共同检索词"), 3)

    def test_chinese_search_and_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "sessions" / "s.jsonl"
            write_jsonl(
                source_path,
                [
                    {"type": "user", "sessionId": "s1", "message": {"role": "user", "content": "用户记忆不应该绑定特定软件"}},
                    {"type": "assistant", "sessionId": "s1", "message": {"role": "assistant", "content": "建立跨智能体索引。"}},
                ],
            )
            adapter = ClaudeAdapter(root / "sessions")
            with ConversationIndex(root / "index.db", adapters=[adapter]) as index:
                report = index.index_all()
                self.assertEqual(report["indexed"], 1)
                self.assertEqual(index.count_search("", source="claude"), 1)
                self.assertEqual(index.count_search("记忆不应该绑定", source="claude"), 1)
                results = index.search("记忆不应该绑定")
                self.assertEqual(results[0]["id"], "claude:s1")
                self.assertEqual(results[0]["match_ordinal"], 0)

                assistant_results = index.search("建立跨智能体索引")
                self.assertEqual(assistant_results[0]["match_ordinal"], 1)
                focused = SyncAnythingService(index).get_session(
                    "claude:s1",
                    max_chars=15,
                    focus_ordinal=0,
                    focus_query="用户记忆",
                )
                assert focused is not None
                self.assertEqual([message["ordinal"] for message in focused["messages"]], [0])
                self.assertIn("用户记忆", focused["messages"][0]["text"])

                server = McpServer(index)
                initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
                assert initialized is not None
                self.assertEqual(initialized["result"]["serverInfo"]["name"], "syncanything")
                called = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "get_session", "arguments": {"session_id": "claude:s1"}},
                    }
                )
                assert called is not None
                text = called["result"]["content"][0]["text"]
                self.assertIn("用户记忆不应该绑定特定软件", text)
                self.assertNotIn("system", text)


class _FakeCiteAnythingServer:
    """Minimal stand-in for the CiteAnything conversations API."""

    def __init__(self, legacy: bool = False, account_uid: str = "u_acct1") -> None:
        self.conversations: dict[str, dict] = {}
        self.deleted: dict[str, str] = {}  # id -> deleted_at
        self.account_uid = account_uid
        self.legacy = legacy  # emulate a server predating limit/offset/total
        self.list_hits = 0
        self.detail_hits = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_GET(self):
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if parsed.path == "/api/conversations":
                    outer.list_hits += 1
                    include_deleted = query.get("include_deleted", ["false"])[0] == "true"
                    entries = [
                        {k: v for k, v in c.items() if k != "events"}
                        for c in outer.conversations.values()
                    ]
                    if include_deleted:
                        entries += [
                            {
                                "id": int(cid),
                                "deleted": True,
                                "updated_at": when,
                                "deleted_at": when,
                            }
                            for cid, when in outer.deleted.items()
                        ]
                    summaries = sorted(
                        entries, key=lambda c: c["updated_at"], reverse=True
                    )
                    if outer.legacy:
                        body = {"conversations": summaries[:50]}
                    else:
                        limit = int(query.get("limit", ["50"])[0])
                        if limit > 100:
                            # Mirror the real endpoint's Query(le=100) rejection.
                            self.send_response(422)
                            self.send_header("Content-Length", "0")
                            self.end_headers()
                            return
                        offset = int(query.get("offset", ["0"])[0])
                        body = {
                            "conversations": summaries[offset : offset + limit],
                            "account": {"uid": outer.account_uid},
                            "total": len(summaries),
                            "limit": limit,
                            "offset": offset,
                        }
                else:
                    outer.detail_hits += 1
                    body = outer.conversations[parsed.path.rsplit("/", 1)[-1]]
                data = json.dumps(body).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def add(self, number: int, updated_at: str) -> None:
        self.conversations[str(number)] = {
            "id": number,
            "title": f"会话 {number}",
            "session_id": f"s{number}",
            "created_at": "2026-08-01T00:00:00Z",
            "updated_at": updated_at,
            "events": [
                {"type": "user", "message": {"role": "user", "content": f"内容 {number}"}}
            ],
        }

    def delete(self, number: int, deleted_at: str = "2026-09-01T00:00:00Z") -> None:
        """Delete a conversation the way the server does: content gone, tombstone kept."""
        self.conversations.pop(str(number), None)
        self.deleted[str(number)] = deleted_at

    def reset_counters(self) -> None:
        self.list_hits = 0
        self.detail_hits = 0

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class CiteAnythingIncrementalSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _FakeCiteAnythingServer()
        self.addCleanup(self.server.close)
        self.cache = Path(tempfile.mkdtemp())
        self.connection = CiteAnythingConnection(
            id="test", name="Test", base_url=self.server.base_url
        )
        for number in range(60):
            self.server.add(number, f"2026-08-01T00:{number:02d}:00Z")

    def _namespace(self) -> str:
        """The account-derived cache/session namespace for the fake server."""
        return CiteAnythingAdapter.account_namespace(
            self.server.base_url, self.server.account_uid
        )

    def _sync(self) -> tuple[CiteAnythingAdapter, int]:
        adapter = CiteAnythingAdapter(
            cache_root=self.cache, connections=[(self.connection, "key")]
        )
        return adapter, len(list(adapter.discover()))

    def test_unchanged_conversations_are_not_refetched(self) -> None:
        self.server.reset_counters()
        _, cached = self._sync()
        self.assertEqual(cached, 60)
        self.assertEqual(self.server.detail_hits, 60)  # cold sync must fetch all
        self.assertEqual(self.server.list_hits, 1)  # one page covers the history

        self.server.reset_counters()
        adapter, cached = self._sync()
        self.assertEqual(cached, 60)
        self.assertEqual(self.server.detail_hits, 0)  # nothing changed, nothing fetched
        self.assertEqual(adapter.skipped_unchanged, 60)

    def test_only_modified_conversations_are_refetched(self) -> None:
        self._sync()
        self.server.conversations["7"]["updated_at"] = "2026-08-02T09:00:00Z"
        self.server.reset_counters()
        adapter, _ = self._sync()
        self.assertEqual(self.server.detail_hits, 1)
        self.assertEqual(adapter.skipped_unchanged, 59)

    def test_conversations_deleted_upstream_are_pruned(self) -> None:
        self._sync()
        del self.server.conversations["9"]
        adapter, cached = self._sync()
        self.assertEqual(cached, 59)
        self.assertEqual(adapter.pruned, 1)

    def test_pagination_reaches_beyond_one_page(self) -> None:
        for number in range(60, 260):
            self.server.add(number, f"2026-08-03T{(number // 60):02d}:{number % 60:02d}:00Z")
        self.server.reset_counters()
        _, cached = self._sync()
        self.assertEqual(cached, 260)
        self.assertGreater(self.server.list_hits, 1)

    def test_tombstone_removes_the_cached_conversation(self) -> None:
        _, cached = self._sync()
        self.assertEqual(cached, 60)

        self.server.delete(9)
        self.server.reset_counters()
        adapter, cached = self._sync()
        self.assertEqual(cached, 59)
        self.assertEqual(adapter.pruned, 1)
        self.assertEqual(self.server.detail_hits, 0)  # a tombstone needs no fetch

    def test_tombstone_is_honoured_without_full_enumeration(self) -> None:
        # Absence-based pruning needs the whole history; an explicit tombstone
        # does not, which is the point of recording deletions server-side.
        self._sync()
        self.server.delete(9)
        self.server.legacy = True  # no total, so the absence prune cannot run
        adapter, cached = self._sync()
        self.assertEqual(cached, 59)
        self.assertEqual(adapter.pruned, 1)

    def test_timestamp_format_change_does_not_refetch(self) -> None:
        self._sync()
        # The server switches from "+00:00" to "Z" for the very same instants.
        for conversation in self.server.conversations.values():
            conversation["updated_at"] = conversation["updated_at"].replace("Z", "+00:00")
        self.server.reset_counters()
        adapter, _ = self._sync()
        self.assertEqual(self.server.detail_hits, 0)
        self.assertEqual(adapter.skipped_unchanged, 60)

    def test_cache_is_keyed_by_account_not_connection(self) -> None:
        self._sync()
        expected = self.cache / self._namespace()
        self.assertTrue(expected.is_dir())
        self.assertEqual(len(list(expected.glob("conversation-*.json"))), 60)

        # Re-adding the account mints a new connection id; the cache must persist.
        reconnected = CiteAnythingConnection(
            id="test-a-different-uuid", name="Test", base_url=self.server.base_url
        )
        adapter = CiteAnythingAdapter(
            cache_root=self.cache, connections=[(reconnected, "key")]
        )
        self.server.reset_counters()
        list(adapter.discover())
        self.assertEqual(self.server.detail_hits, 0)
        self.assertEqual(adapter.skipped_unchanged, 60)

    def test_session_ids_survive_a_reconnect(self) -> None:
        self._sync()
        snapshot = next((self.cache / self._namespace()).glob("conversation-*.json"))
        adapter = CiteAnythingAdapter(cache_root=self.cache, connections=[])
        session = adapter.parse(snapshot)
        assert session is not None
        # Namespaced by account, so nothing about it depends on the connection.
        self.assertTrue(session.id.startswith(f"citeanything:{self._namespace()}:"))
        self.assertNotIn("test", session.id)

    def test_legacy_cache_directory_is_adopted(self) -> None:
        # A directory written before the server exposed an account uid.
        legacy = self.cache / "0123456789ab"
        legacy.mkdir(parents=True)
        (legacy / "conversation-1.json").write_text(
            json.dumps(
                {
                    "id": 1,
                    "title": "旧的会话",
                    "updated_at": "2026-08-01T00:01:00Z",
                    "_syncanything_base_url": self.server.base_url,
                    "_syncanything_connection_id": "test-old-uuid",
                    "events": [
                        {"type": "user", "message": {"role": "user", "content": "内容 1"}}
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.server.reset_counters()
        adapter, _ = self._sync()

        self.assertFalse(legacy.exists())
        adopted = self.cache / self._namespace()
        self.assertTrue((adopted / "conversation-1.json").exists())
        self.assertEqual(adapter.adopted, 1)
        # Adopted, not re-downloaded: conversation 1 was already current.
        self.assertEqual(self.server.detail_hits, 59)

        payload = json.loads((adopted / "conversation-1.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["_syncanything_account_uid"], "u_acct1")

    def test_ambiguous_legacy_directories_are_left_alone(self) -> None:
        # Two accounts on one site: guessing would file one under the other.
        for name in ("aaaaaaaaaaaa", "bbbbbbbbbbbb"):
            directory = self.cache / name
            directory.mkdir(parents=True)
            (directory / "conversation-1.json").write_text(
                json.dumps(
                    {
                        "id": 1,
                        "updated_at": "2026-08-01T00:01:00Z",
                        "_syncanything_base_url": self.server.base_url,
                        "events": [],
                    }
                ),
                encoding="utf-8",
            )
        adapter, _ = self._sync()
        self.assertEqual(adapter.adopted, 0)
        self.assertTrue((self.cache / "aaaaaaaaaaaa").exists())
        self.assertTrue((self.cache / "bbbbbbbbbbbb").exists())

    def test_legacy_server_neither_loops_nor_prunes(self) -> None:
        self._sync()
        self.server.legacy = True
        self.server.reset_counters()
        adapter, cached = self._sync()
        # A server that ignores offset returns the same page forever; the sync
        # must stop, and must not treat that partial list as grounds to prune.
        self.assertEqual(self.server.list_hits, 1)
        self.assertEqual(adapter.pruned, 0)
        self.assertEqual(cached, 60)


class RefreshTests(unittest.TestCase):
    def _write_session(self, sessions: Path, number: int, text: str) -> None:
        write_jsonl(
            sessions / f"s{number}.jsonl",
            [{"type": "user", "sessionId": f"s{number}", "message": {"role": "user", "content": text}}],
        )

    def test_refresh_picks_up_sessions_written_after_indexing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = root / "sessions"
            sessions.mkdir()
            self._write_session(sessions, 1, "斑马鱼实验的原始讨论")
            adapter = ClaudeAdapter(sessions)
            with ConversationIndex(root / "index.db", adapters=[adapter]) as index:
                index.index_all()
                self.assertEqual(len(index.search("斑马鱼实验")), 1)

                # A conversation created after the last index must still be findable.
                self._write_session(sessions, 2, "独角鲸是全新的会话")
                self.assertEqual(len(index.search("独角鲸")), 0)
                self.assertIsNotNone(index.refresh(force=True))
                self.assertEqual(len(index.search("独角鲸")), 1)

    def test_refresh_is_ttl_gated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = root / "sessions"
            sessions.mkdir()
            self._write_session(sessions, 1, "第一个会话的内容")
            with ConversationIndex(root / "index.db", adapters=[ClaudeAdapter(sessions)]) as index:
                self.assertIsNotNone(index.refresh(force=True))
                # Within the window a second refresh is a no-op.
                self.assertIsNone(index.refresh(max_age_seconds=600))
                self.assertIsNotNone(index.refresh(max_age_seconds=0))

    def test_refresh_syncs_remote_adapters_with_throttle(self) -> None:
        class StubRemote(CodexAdapter):
            name = "citeanything"
            is_remote = True

            def __init__(self, root: Path) -> None:
                super().__init__(root)
                self.discover_calls = 0

            def discover(self):
                self.discover_calls += 1
                return super().discover()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local_root = root / "claude"
            remote_root = root / "remote"
            local_root.mkdir()
            remote_root.mkdir()
            self._write_session(local_root, 1, "本地会话的内容")
            write_jsonl(
                remote_root / "rollout-2026-08-03T10-00-00-019f0000-0000-7000-0000-000000000009.jsonl",
                [
                    {"type": "session_meta", "timestamp": "2026-08-03T10:00:00Z", "payload": {"id": "r1", "cwd": "/w"}},
                    {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "远程产品里的会话"}]}},
                ],
            )
            remote = StubRemote(remote_root)
            with ConversationIndex(
                root / "index.db", adapters=[ClaudeAdapter(local_root), remote]
            ) as index:
                index.index_all()
                self.assertEqual(len(index.search("远程产品里的会话")), 1)
                calls_after_full_index = remote.discover_calls

                # force=True always syncs remote adapters.
                index.refresh(force=True)
                self.assertEqual(remote.discover_calls, calls_after_full_index + 1)
                self.assertEqual(len(index.search("远程产品里的会话")), 1)

                # A non-forced refresh within the interval skips the remote sync.
                index.refresh(force=False)
                self.assertEqual(remote.discover_calls, calls_after_full_index + 1)

    def test_index_report_separates_unchanged_from_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = root / "sessions"
            sessions.mkdir()
            self._write_session(sessions, 1, "有内容的真实会话")
            # A transcript with no visible user/assistant prose parses to nothing.
            write_jsonl(sessions / "empty.jsonl", [{"type": "summary", "summary": "no prose"}])
            with ConversationIndex(root / "index.db", adapters=[ClaudeAdapter(sessions)]) as index:
                first = index.index_all()
                self.assertEqual(first["indexed"], 1)
                self.assertEqual(first["unchanged"], 0)
                self.assertEqual(first["empty"], 1)

                second = index.index_all()
                self.assertEqual(second["indexed"], 0)
                self.assertEqual(second["unchanged"], 1)
                self.assertEqual(second["empty"], 1)
                self.assertEqual(second["skipped"], 2)


class TextMetricsTests(unittest.TestCase):
    def test_cjk_counts_one_word_per_character_and_latin_by_word(self) -> None:
        metrics = measure("你好世界 hello wide world")
        self.assertEqual(metrics.characters, 18)  # whitespace excluded
        self.assertEqual(metrics.words, 7)  # 4 ideographs + 3 Latin words
        self.assertEqual(metrics.bytes, len("你好世界 hello wide world".encode("utf-8")))

    def test_indentation_does_not_inflate_the_word_count(self) -> None:
        dense = measure("def f():\nreturn 1")
        indented = measure("    def f():\n        return 1")
        self.assertEqual(dense.characters, indented.characters)
        self.assertEqual(dense.words, indented.words)
        # Bytes stay honest about what is actually stored.
        self.assertGreater(indented.bytes, dense.bytes)

    def test_empty_text_measures_to_zero(self) -> None:
        self.assertEqual(measure(""), TextMetrics())

    def test_metrics_add_componentwise(self) -> None:
        self.assertEqual(
            measure("你好") + measure("world"),
            measure("你好") + measure("world"),
        )
        combined = measure("你好") + measure("world")
        self.assertEqual(combined.words, measure("你好").words + measure("world").words)

    def test_book_equivalents_compare_like_units(self) -> None:
        books = book_equivalents(characters=1_460_000, words=1_174_574)
        chinese = {book["title"]: book for book in books["zh"]}
        english = {book["title"]: book for book in books["en"]}
        # 1,460,000 characters is exactly two copies of the 730,000-character 红楼梦.
        self.assertEqual(chinese["红楼梦"]["unit"], "characters")
        self.assertEqual(chinese["红楼梦"]["equivalent"], 2.0)
        # 1,174,574 words is exactly two copies of War and Peace.
        self.assertEqual(english["War and Peace"]["unit"], "words")
        self.assertEqual(english["War and Peace"]["equivalent"], 2.0)


class StatsTests(unittest.TestCase):
    def _index_one_session(self, root: Path, text: str) -> ConversationIndex:
        sessions = root / "sessions"
        sessions.mkdir(exist_ok=True)
        write_jsonl(
            sessions / "s1.jsonl",
            [{"type": "user", "sessionId": "s1", "message": {"role": "user", "content": text}}],
        )
        index = ConversationIndex(root / "index.db", adapters=[ClaudeAdapter(sessions)])
        index.index_all()
        return index

    def test_stats_report_text_size_and_book_equivalents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = "这是一次关于斑马鱼实验的讨论 with some English too"
            with self._index_one_session(root, text) as index:
                stats = index.stats()
                expected = measure(text)
                self.assertEqual(stats["sessions"], 1)
                self.assertEqual(stats["characters"], expected.characters)
                self.assertEqual(stats["words"], expected.words)
                self.assertEqual(stats["tokens"], expected.tokens)
                self.assertEqual(stats["text_bytes"], expected.bytes)
                # The SQLite file and its write-ahead log are larger than the text.
                self.assertGreater(stats["storage_bytes"], stats["text_bytes"])
                self.assertIn("红楼梦", [book["title"] for book in stats["books"]["zh"]])
                self.assertIn("War and Peace", [book["title"] for book in stats["books"]["en"]])

    def test_per_source_rows_carry_text_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self._index_one_session(root, "斑马鱼实验的原始讨论") as index:
                (row,) = index.stats()["sources"]
                self.assertEqual(row["source"], "claude")
                self.assertEqual(row["sessions"], 1)
                self.assertGreater(row["characters"], 0)
                self.assertGreater(row["tokens"], 0)

    def test_reindexing_a_changed_session_replaces_rather_than_adds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = root / "sessions"
            sessions.mkdir()
            path = sessions / "s1.jsonl"
            write_jsonl(
                path,
                [{"type": "user", "sessionId": "s1", "message": {"role": "user", "content": "短"}}],
            )
            with ConversationIndex(root / "index.db", adapters=[ClaudeAdapter(sessions)]) as index:
                index.index_all()
                first = index.stats()["characters"]
                write_jsonl(
                    path,
                    [
                        {
                            "type": "user",
                            "sessionId": "s1",
                            "message": {"role": "user", "content": "短" * 40},
                        }
                    ],
                )
                index.index_all()
                self.assertEqual(index.stats()["characters"], 40)
                self.assertNotEqual(index.stats()["characters"], first)

    def test_index_without_metric_columns_is_migrated_and_backfilled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "legacy.db"
            legacy = sqlite3.connect(db_path)
            legacy.executescript(
                """
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, native_id TEXT NOT NULL,
                    title TEXT NOT NULL, cwd TEXT, started_at TEXT, updated_at TEXT,
                    source_path TEXT NOT NULL UNIQUE, message_count INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
                    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE messages (
                    session_id TEXT NOT NULL, ordinal INTEGER NOT NULL, role TEXT NOT NULL,
                    timestamp TEXT, text TEXT NOT NULL, PRIMARY KEY(session_id, ordinal)
                );
                """
            )
            legacy.execute(
                "INSERT INTO sessions VALUES ('claude:s1','claude','s1','Legacy',NULL,NULL,NULL,"
                "'/tmp/s1.jsonl',1,'fp','{}',CURRENT_TIMESTAMP)"
            )
            legacy.execute(
                "INSERT INTO messages VALUES ('claude:s1',0,'user',NULL,'斑马鱼实验的原始讨论')"
            )
            legacy.commit()
            legacy.close()

            # Opening the index must add the columns and fill them from the
            # messages already stored, with no reindex of the source files.
            with ConversationIndex(db_path, adapters=[]) as index:
                stats = index.stats()
                expected = measure("斑马鱼实验的原始讨论")
                self.assertEqual(stats["characters"], expected.characters)
                self.assertEqual(stats["tokens"], expected.tokens)


class InterfaceTranslationTests(unittest.TestCase):
    """The web interface ships two languages; neither may drift from the other."""

    LANGUAGE_HEADER = re.compile(r'^  "?([\w-]+)"?: \{$')
    ENTRY = re.compile(r'^    "([\w.]+)":')

    def _tables(self) -> dict[str, set[str]]:
        source = files("syncanything.static").joinpath("app.js").read_text(encoding="utf-8")
        body = source.split("const translations = {", 1)[1].split("\n};", 1)[0]
        tables: dict[str, set[str]] = {}
        current: str | None = None
        for line in body.splitlines():
            header = self.LANGUAGE_HEADER.match(line)
            if header:
                current = header.group(1)
                tables[current] = set()
                continue
            entry = self.ENTRY.match(line)
            if entry and current:
                tables[current].add(entry.group(1))
        return tables

    def test_both_languages_define_the_same_keys(self) -> None:
        tables = self._tables()
        self.assertEqual(set(tables), {"zh-Hans", "en"})
        self.assertEqual(
            tables["zh-Hans"],
            tables["en"],
            "translation keys drifted between zh-Hans and en",
        )

    def test_every_markup_binding_has_a_translation(self) -> None:
        markup = files("syncanything.static").joinpath("index.html").read_text(encoding="utf-8")
        keys = set(
            re.findall(
                r'data-i18n(?:-html|-placeholder|-aria-label)?="([\w.]+)"',
                markup,
            )
        )
        self.assertTrue(keys, "index.html declares no translated strings")
        for language, table in self._tables().items():
            self.assertEqual(set(), keys - table, f"{language} is missing markup keys")

    def test_literal_lookups_in_script_have_translations(self) -> None:
        source = files("syncanything.static").joinpath("app.js").read_text(encoding="utf-8")
        # Template-literal keys such as t(`error.${code}`) are resolved with a
        # fallback at runtime, so only literal lookups are checked here. The
        # lookbehind keeps calls like createElement("div") out of the match.
        keys = set(re.findall(r'(?<![\w$.])t\("([\w.]+)"', source))
        for language, table in self._tables().items():
            self.assertEqual(set(), keys - table, f"{language} is missing script keys")


if __name__ == "__main__":
    unittest.main()
