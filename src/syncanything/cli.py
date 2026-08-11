from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from syncanything import __version__
from syncanything.index import ConversationIndex, default_db_path
from syncanything.mcp import run_mcp
from syncanything.service import SyncAnythingService
from syncanything.shortcut import install_shortcut, shortcut_status, uninstall_shortcut
from syncanything.sources import local_adapters
from syncanything.web import serve
from syncanything.works import (
    WorksConflictError,
    WorksSyncError,
    configured_works_clients,
    pull_work,
    push_work,
    select_checkout_client,
    select_works_client,
)

READ_COMMANDS = frozenset({"search", "list", "show", "reference", "status"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="syncanything",
        description="Search and reference local conversations across AI coding tools.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument("--db", help="Override the SQLite index path.")
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Skip the automatic local re-scan that keeps read commands current.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Refresh the local conversation index.")
    index_parser.add_argument("--force", action="store_true", help="Reparse unchanged files.")
    index_parser.add_argument(
        "--local",
        action="store_true",
        help="Only scan local files; skip connected remote products.",
    )
    index_parser.add_argument("--json", action="store_true")

    search_parser = subparsers.add_parser("search", help="Search indexed sessions.")
    search_parser.add_argument("query")
    search_parser.add_argument(
        "--source", choices=["claude", "codex", "cursor", "kimi", "pi", "citeanything"]
    )
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--json", action="store_true")

    list_parser = subparsers.add_parser("list", help="List recent sessions.")
    list_parser.add_argument(
        "--source", choices=["claude", "codex", "cursor", "kimi", "pi", "citeanything"]
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

    works_parser = subparsers.add_parser(
        "works", help="Pull and push CiteAnything works for local editing."
    )
    works_commands = works_parser.add_subparsers(dest="works_command", required=True)
    works_list = works_commands.add_parser("list", help="List durable CiteAnything works.")
    works_list.add_argument("--connection")
    works_list.add_argument("--json", action="store_true")
    works_pull = works_commands.add_parser("pull", help="Download one work into a new directory.")
    works_pull.add_argument("work", help="Work name or outputs/<name> path.")
    works_pull.add_argument("destination", nargs="?")
    works_pull.add_argument("--connection")
    works_pull.add_argument("--json", action="store_true")
    works_push = works_commands.add_parser("push", help="Upload a modified local work.")
    works_push.add_argument("directory", nargs="?", default=".")
    works_push.add_argument("--connection")
    works_push.add_argument("--json", action="store_true")

    shortcut_parser = subparsers.add_parser(
        "shortcut", help="Install or manage the macOS global search shortcut."
    )
    shortcut_commands = shortcut_parser.add_subparsers(
        dest="shortcut_command", required=True
    )
    shortcut_commands.add_parser(
        "install", help="Start SyncAnything at login and register Control+Command+K."
    )
    shortcut_commands.add_parser("uninstall", help="Remove the login agents and hotkey.")
    shortcut_status_parser = shortcut_commands.add_parser(
        "status", help="Show whether the global shortcut is installed and running."
    )
    shortcut_status_parser.add_argument("--json", action="store_true")

    subparsers.add_parser("mcp", help="Run the agent-native MCP server over stdio.")
    return parser


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _print_table(results: list[dict[str, Any]]) -> None:
    for result in results:
        updated = (result.get("updated_at") or "")[:19].replace("T", " ")
        print(f"{result['id']}\t{updated}\t{result['title']}")
        snippet = result.get("snippet")
        if snippet:
            clean = snippet.replace("<mark>", "").replace("</mark>", "").replace("\n", " ")
            print(f"  {clean[:220]}")


def _configure_home_from_db(db_path: Path) -> None:
    if "SYNCANYTHING_HOME" not in os.environ:
        os.environ["SYNCANYTHING_HOME"] = str(db_path.parent)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    db_path = Path(args.db).expanduser() if args.db else default_db_path()
    if args.db:
        _configure_home_from_db(db_path)
    if args.command == "works":
        return _run_works(args)
    if args.command == "shortcut":
        return _run_shortcut(args)
    with ConversationIndex(db_path) as index:
        service = SyncAnythingService(index)
        # Read commands re-scan local sources first so results always reflect the
        # conversations on disk right now. Remote products stay on `index`/`serve`.
        if args.command in READ_COMMANDS and not args.no_refresh:
            index.refresh()
        if args.command == "index":
            report = index.index_all(
                adapters=local_adapters() if args.local else None, force=args.force
            )
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(
                    f"Indexed {report['indexed']}; unchanged {report['unchanged']}; "
                    f"empty {report['empty']}; removed {report['removed']}; "
                    f"errors {len(report['errors'])}"
                )
                for source, state in report["sources"].items():
                    print(
                        f"  {source}: found {state['discovered']}, indexed {state['indexed']}, "
                        f"unchanged {state['unchanged']}, empty {state['empty']}, "
                        f"errors {state['errors']}"
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
                print(
                    f"  {stats['characters']:,} characters · {stats['words']:,} words · "
                    f"~{stats['tokens']:,} tokens (estimated)"
                )
                print(
                    f"  {_human_bytes(stats['text_bytes'])} of text · "
                    f"{_human_bytes(stats['storage_bytes'])} on disk"
                )
                book = stats["books"]["en"][0]
                print(f"  about {book['equivalent']:g} x {book['title']}")
                for source in stats["sources"]:
                    print(
                        f"  {source['source']}: {source['sessions']} sessions, "
                        f"{source['messages']} messages, {source['characters']:,} characters"
                    )
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


def _run_shortcut(args: argparse.Namespace) -> int:
    try:
        if args.shortcut_command == "install":
            state = install_shortcut()
            print(f"Installed SyncAnything global search: {state['shortcut']}")
            print(f"Search page: {state['url']}")
            return 0
        if args.shortcut_command == "uninstall":
            uninstall_shortcut()
            print("Removed the SyncAnything global search shortcut")
            return 0
        state = shortcut_status()
        if args.json:
            print(json.dumps(state, ensure_ascii=False, indent=2))
        else:
            installed = "installed" if state["installed"] else "not installed"
            running = "running" if state["hotkey_loaded"] else "not running"
            server = "ready" if state["server_reachable"] else "not ready"
            print(f"{state['shortcut']} · {installed} · {running} · server {server}")
            print(state["url"])
        return 0 if state["installed"] else 1
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        print(f"Could not configure the SyncAnything shortcut: {detail.strip()}", file=sys.stderr)
        return 2


def _run_works(args: argparse.Namespace) -> int:
    try:
        clients = configured_works_clients()
        client = (
            select_checkout_client(clients, Path(args.directory), args.connection)
            if args.works_command == "push"
            else select_works_client(clients, args.connection)
        )
        if args.works_command == "list":
            payload = client.list_works()
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                revision = payload.get("revision") or "no checkpoint"
                print(f"{client.connection.name} · revision {revision}")
                for work in payload.get("works", []):
                    print(
                        f"{work.get('path', '')}\t{work.get('file_count', 0)} files\t"
                        f"{_human_bytes(int(work.get('total_bytes', 0)))}"
                    )
            return 0
        if args.works_command == "pull":
            result = pull_work(
                client,
                args.work,
                Path(args.destination) if args.destination else None,
            )
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"Pulled {result['work_path']} -> {result['local_path']}")
                print(f"Base revision: {result['revision']}")
            return 0
        if args.works_command == "push":
            result = push_work(client, Path(args.directory))
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"Pushed {result['work_path']} from {result['local_path']}")
                print(f"New revision: {result['revision']}")
            return 0
    except WorksConflictError as error:
        suffix = f" Current revision: {error.current_revision}." if error.current_revision else ""
        print(
            f"Push conflict: {error}.{suffix} Pull the work again, reapply your edits, and push.",
            file=sys.stderr,
        )
        return 3
    except WorksSyncError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
