from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from syncanything.index import ConversationIndex, default_db_path
from syncanything.mcp import run_mcp
from syncanything.service import SyncAnythingService
from syncanything.web import serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="syncanything",
        description="Search and reference local conversations across AI coding tools.",
    )
    parser.add_argument("--db", help="Override the SQLite index path.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Refresh the local conversation index.")
    index_parser.add_argument("--force", action="store_true", help="Reparse unchanged files.")
    index_parser.add_argument("--json", action="store_true")

    search_parser = subparsers.add_parser("search", help="Search indexed sessions.")
    search_parser.add_argument("query")
    search_parser.add_argument(
        "--source", choices=["claude", "codex", "kimi", "pi", "citeanything"]
    )
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--json", action="store_true")

    list_parser = subparsers.add_parser("list", help="List recent sessions.")
    list_parser.add_argument(
        "--source", choices=["claude", "codex", "kimi", "pi", "citeanything"]
    )
    list_parser.add_argument("--limit", type=int, default=30)
    list_parser.add_argument("--json", action="store_true")

    show_parser = subparsers.add_parser("show", help="Render one session as readable Markdown.")
    show_parser.add_argument("session_id")
    show_parser.add_argument("--last", type=int, dest="last_messages")
    show_parser.add_argument("--max-chars", type=int, default=50_000)
    show_parser.add_argument("--json", action="store_true")

    path_parser = subparsers.add_parser("reference", help="Return a session URI and original path.")
    path_parser.add_argument("session_id")
    path_parser.add_argument("--json", action="store_true")

    status_parser = subparsers.add_parser("status", help="Show index statistics.")
    status_parser.add_argument("--json", action="store_true")

    serve_parser = subparsers.add_parser("serve", help="Start the local search interface.")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=7331)
    serve_parser.add_argument("--no-index", action="store_true")

    subparsers.add_parser("mcp", help="Run the agent-native MCP server over stdio.")
    return parser


def _print_table(results: list[dict[str, Any]]) -> None:
    for result in results:
        updated = (result.get("updated_at") or "")[:19].replace("T", " ")
        print(f"{result['id']}\t{updated}\t{result['title']}")
        snippet = result.get("snippet")
        if snippet:
            clean = snippet.replace("<mark>", "").replace("</mark>", "").replace("\n", " ")
            print(f"  {clean[:220]}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from pathlib import Path

    db_path = Path(args.db).expanduser() if args.db else default_db_path()
    with ConversationIndex(db_path) as index:
        service = SyncAnythingService(index)
        if args.command == "index":
            report = index.index_all(force=args.force)
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(
                    f"Indexed {report['indexed']}; unchanged {report['skipped']}; "
                    f"removed {report['removed']}; errors {len(report['errors'])}"
                )
                for source, state in report["sources"].items():
                    print(
                        f"  {source}: found {state['discovered']}, indexed {state['indexed']}, "
                        f"unchanged {state['skipped']}, errors {state['errors']}"
                    )
                    if state.get("sync_error"):
                        print(f"    connection warning: {state['sync_error']}")
            return 1 if report["errors"] else 0
        if args.command == "search":
            results = service.search_sessions(args.query, source=args.source, limit=args.limit)
            if args.json:
                print(json.dumps(results, ensure_ascii=False, indent=2))
            else:
                _print_table(results)
            return 0
        if args.command == "list":
            results = service.list_sessions(source=args.source, limit=args.limit)
            if args.json:
                print(json.dumps(results, ensure_ascii=False, indent=2))
            else:
                _print_table(results)
            return 0
        if args.command == "show":
            session = service.get_session(
                args.session_id, last_messages=args.last_messages, max_chars=args.max_chars
            )
            if session is None:
                print(f"Session not found: {args.session_id}", file=sys.stderr)
                return 2
            if args.json:
                print(json.dumps(session, ensure_ascii=False, indent=2))
            else:
                print(service.render_markdown(session), end="")
            return 0
        if args.command == "reference":
            reference = service.get_reference(args.session_id)
            if reference is None:
                print(f"Session not found: {args.session_id}", file=sys.stderr)
                return 2
            if args.json:
                print(json.dumps(reference, ensure_ascii=False, indent=2))
            else:
                print(reference["uri"])
                print(reference["path"])
            return 0
        if args.command == "status":
            stats = index.stats()
            if args.json:
                print(json.dumps(stats, ensure_ascii=False, indent=2))
            else:
                print(f"{stats['sessions']} sessions · {stats['messages']} messages · {stats['database']}")
                for source in stats["sources"]:
                    print(f"  {source['source']}: {source['sessions']} sessions, {source['messages']} messages")
            return 0
        if args.command == "serve":
            if not args.no_index:
                index.index_all()
            serve(index, host=args.host, port=args.port)
            return 0
        if args.command == "mcp":
            if index.stats()["sessions"] == 0:
                index.index_all()
            run_mcp(index)
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
