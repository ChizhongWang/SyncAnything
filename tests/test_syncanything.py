from __future__ import annotations

import json
import os
import platform
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
from syncanything.service import SyncAnythingService
from syncanything.sources.citeanything import CiteAnythingAdapter
from syncanything.sources.claude import ClaudeAdapter
from syncanything.sources.codex import CodexAdapter
from syncanything.sources.kimi import KimiAdapter
from syncanything.sources.pi import PiAdapter


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

    def __init__(self, legacy: bool = False) -> None:
        self.conversations: dict[str, dict] = {}
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
                    ordered = sorted(
                        outer.conversations.values(),
                        key=lambda c: c["updated_at"],
                        reverse=True,
                    )
                    summaries = [
                        {k: v for k, v in c.items() if k != "events"} for c in ordered
                    ]
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
            "created_at": "2026-08-01T00:00:00+00:00",
            "updated_at": updated_at,
            "events": [
                {"type": "user", "message": {"role": "user", "content": f"内容 {number}"}}
            ],
        }

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
            self.server.add(number, f"2026-08-01T00:{number:02d}:00+00:00")

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
        self.server.conversations["7"]["updated_at"] = "2026-08-02T09:00:00+00:00"
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
            self.server.add(number, f"2026-08-03T{(number // 60):02d}:{number % 60:02d}:00+00:00")
        self.server.reset_counters()
        _, cached = self._sync()
        self.assertEqual(cached, 260)
        self.assertGreater(self.server.list_hits, 1)

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

    def test_refresh_skips_remote_adapters_and_keeps_their_sessions(self) -> None:
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

                index.refresh(force=True)
                # The remote source is never contacted by a read-path refresh...
                self.assertEqual(remote.discover_calls, calls_after_full_index)
                # ...and its already-indexed sessions survive the local prune.
                self.assertEqual(len(index.search("远程产品里的会话")), 1)

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


if __name__ == "__main__":
    unittest.main()
