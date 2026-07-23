from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from syncanything.index import ConversationIndex
from syncanything.mcp import McpServer
from syncanything.sources.citeanything import CiteAnythingAdapter
from syncanything.sources.claude import ClaudeAdapter
from syncanything.sources.codex import CodexAdapter
from syncanything.sources.kimi import KimiAdapter
from syncanything.sources.pi import PiAdapter


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n")


class AdapterTests(unittest.TestCase):
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
            with ConversationIndex(root / "index.db") as index:
                report = index.index_all([adapter])
                self.assertEqual(report["indexed"], 1)
                results = index.search("记忆不应该绑定")
                self.assertEqual(results[0]["id"], "claude:s1")

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


if __name__ == "__main__":
    unittest.main()
