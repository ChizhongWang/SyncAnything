from __future__ import annotations

import os
import platform
import plistlib
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from syncanything.connections import syncanything_home


SERVER_LABEL = "com.syncanything.server"
HOTKEY_LABEL = "com.syncanything.hotkey"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7331
DEFAULT_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
SHORTCUT_LABEL = "Control + Command + K"


@dataclass(frozen=True, slots=True)
class ShortcutPaths:
    root: Path
    source: Path
    helper: Path
    logs: Path
    server_plist: Path
    hotkey_plist: Path


def shortcut_paths(
    home: Path | None = None,
    launch_agents: Path | None = None,
) -> ShortcutPaths:
    root = (home or syncanything_home()) / "shortcut"
    agents = launch_agents or (Path.home() / "Library" / "LaunchAgents")
    return ShortcutPaths(
        root=root,
        source=root / "SyncAnythingHotkey.m",
        helper=root / "syncanything-hotkey",
        logs=root / "logs",
        server_plist=agents / f"{SERVER_LABEL}.plist",
        hotkey_plist=agents / f"{HOTKEY_LABEL}.plist",
    )


def hotkey_source(url: str = DEFAULT_URL) -> str:
    escaped_url = url.replace("\\", "\\\\").replace('"', '\\"')
    return f"""#import <Cocoa/Cocoa.h>
#import <Carbon/Carbon.h>

static OSStatus handle_hotkey(
    EventHandlerCallRef next_handler,
    EventRef event,
    void *user_data
) {{
    [[NSWorkspace sharedWorkspace] openURL:[NSURL URLWithString:@"{escaped_url}"]];
    return noErr;
}}

int main(int argc, const char *argv[]) {{
    @autoreleasepool {{
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        [NSApp finishLaunching];

        EventTypeSpec event_type = {{kEventClassKeyboard, kEventHotKeyPressed}};
        EventHandlerUPP handler = NewEventHandlerUPP(handle_hotkey);
        OSStatus status = InstallApplicationEventHandler(
            handler,
            1,
            &event_type,
            NULL,
            NULL
        );
        if (status != noErr) {{
            fprintf(stderr, "Could not install SyncAnything hotkey handler: %d\\n", status);
            return 1;
        }}

        EventHotKeyID hotkey_id = {{FOUR_CHAR_CODE('SYNC'), 1}};
        EventHotKeyRef hotkey = NULL;
        status = RegisterEventHotKey(
            kVK_ANSI_K,
            cmdKey | controlKey,
            hotkey_id,
            GetApplicationEventTarget(),
            0,
            &hotkey
        );
        if (status != noErr) {{
            fprintf(stderr, "Could not register Control + Command + K: %d\\n", status);
            return 2;
        }}

        [NSApp run];
    }}
    return 0;
}}
"""


def server_launch_agent(sync_executable: Path, paths: ShortcutPaths) -> dict[str, Any]:
    return {
        "Label": SERVER_LABEL,
        "ProgramArguments": [str(sync_executable), "serve", "--no-index"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "ThrottleInterval": 5,
        "StandardOutPath": str(paths.logs / "server.log"),
        "StandardErrorPath": str(paths.logs / "server-error.log"),
    }


def hotkey_launch_agent(paths: ShortcutPaths) -> dict[str, Any]:
    return {
        "Label": HOTKEY_LABEL,
        "ProgramArguments": [str(paths.helper)],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
        "ThrottleInterval": 5,
        "StandardOutPath": str(paths.logs / "hotkey.log"),
        "StandardErrorPath": str(paths.logs / "hotkey-error.log"),
    }


def _write_plist(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".plist.tmp")
    temporary.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True))
    temporary.replace(path)


def _launch_target(label: str) -> str:
    return f"gui/{os.getuid()}/{label}"


def _launchctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/launchctl", *arguments],
        check=check,
        capture_output=True,
        text=True,
    )


def _service_loaded(label: str) -> bool:
    return _launchctl("print", _launch_target(label), check=False).returncode == 0


def _server_reachable(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def shortcut_status(
    home: Path | None = None,
    launch_agents: Path | None = None,
) -> dict[str, Any]:
    paths = shortcut_paths(home, launch_agents)
    supported = platform.system() == "Darwin"
    return {
        "supported": supported,
        "installed": paths.server_plist.is_file()
        and paths.hotkey_plist.is_file()
        and paths.helper.is_file(),
        "server_loaded": supported and _service_loaded(SERVER_LABEL),
        "hotkey_loaded": supported and _service_loaded(HOTKEY_LABEL),
        "server_reachable": supported and _server_reachable(),
        "shortcut": SHORTCUT_LABEL,
        "url": DEFAULT_URL,
    }


def install_shortcut(sync_executable: Path | None = None) -> dict[str, Any]:
    if platform.system() != "Darwin":
        raise RuntimeError("The global search shortcut is currently available on macOS only")

    executable = sync_executable or Path(shutil.which("syncanything") or sys.argv[0])
    executable = executable.expanduser().resolve()
    if not executable.is_file():
        raise RuntimeError(f"Could not find the SyncAnything executable: {executable}")

    compiler = shutil.which("clang")
    if not compiler:
        raise RuntimeError(
            "C compiler not found. Install Apple's Command Line Tools and try again."
        )

    paths = shortcut_paths()
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.logs.mkdir(parents=True, exist_ok=True)
    module_cache = paths.root / "module-cache"
    module_cache.mkdir(parents=True, exist_ok=True)
    paths.source.write_text(hotkey_source(), encoding="utf-8")
    subprocess.run(
        [
            compiler,
            str(paths.source),
            "-o",
            str(paths.helper),
            "-framework",
            "Carbon",
            "-framework",
            "Cocoa",
            "-fobjc-arc",
            f"-fmodules-cache-path={module_cache}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    paths.helper.chmod(0o755)

    for label in (HOTKEY_LABEL, SERVER_LABEL):
        _launchctl("bootout", _launch_target(label), check=False)

    _write_plist(paths.server_plist, server_launch_agent(executable, paths))
    _write_plist(paths.hotkey_plist, hotkey_launch_agent(paths))
    domain = f"gui/{os.getuid()}"
    _launchctl("bootstrap", domain, str(paths.server_plist))
    _launchctl("bootstrap", domain, str(paths.hotkey_plist))
    return shortcut_status()


def uninstall_shortcut() -> dict[str, Any]:
    if platform.system() != "Darwin":
        raise RuntimeError("The global search shortcut is currently available on macOS only")
    paths = shortcut_paths()
    for label in (HOTKEY_LABEL, SERVER_LABEL):
        _launchctl("bootout", _launch_target(label), check=False)
    for path in (paths.hotkey_plist, paths.server_plist, paths.helper, paths.source):
        path.unlink(missing_ok=True)
    return shortcut_status()
