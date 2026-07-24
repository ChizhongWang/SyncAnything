from __future__ import annotations

import base64
import ctypes
import json
import os
import platform
import re
import subprocess
import uuid
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


KEYCHAIN_SERVICE = "SyncAnything.CiteAnything"
SITE_URLS = {
    "international": "https://citeanything.veri-glow.com",
    "china": "https://citeanything.cn",
}


def syncanything_home() -> Path:
    configured = os.environ.get("SYNCANYTHING_HOME")
    if configured:
        return Path(configured).expanduser()
    try:
        return Path.home() / ".syncanything"
    except RuntimeError:
        return Path.cwd() / ".syncanything"


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


CRYPTPROTECT_UI_FORBIDDEN = 0x1


def _windows_crypto() -> tuple[Any, Any, Any]:
    """Return type-safe DPAPI functions, loaded only on Windows."""
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    protect = crypt32.CryptProtectData
    protect.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    protect.restype = wintypes.BOOL

    unprotect = crypt32.CryptUnprotectData
    unprotect.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    unprotect.restype = wintypes.BOOL

    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    return protect, unprotect, local_free


def _windows_secret_path(home: Path, connection_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", connection_id)
    return home / "secrets" / f"citeanything-{safe_id}.dpapi"


def _blob_from_bytes(data: bytes) -> tuple[DATA_BLOB, ctypes.Array[ctypes.c_ubyte]]:
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    return DATA_BLOB(len(data), buffer), buffer


def _bytes_from_blob(blob: DATA_BLOB, local_free: Any) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        local_free(ctypes.cast(blob.pbData, ctypes.c_void_p))


def _protect_windows_secret(secret: str) -> str:
    protect, _, local_free = _windows_crypto()
    plain_blob, plain_buffer = _blob_from_bytes(secret.encode("utf-8"))
    encrypted_blob = DATA_BLOB()
    ok = protect(
        ctypes.byref(plain_blob),
        "SyncAnything CiteAnything API key",
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(encrypted_blob),
    )
    _ = plain_buffer
    if not ok:
        raise ctypes.WinError()
    return base64.b64encode(_bytes_from_blob(encrypted_blob, local_free)).decode("ascii")


def _unprotect_windows_secret(encoded: str) -> str:
    _, unprotect, local_free = _windows_crypto()
    encrypted = base64.b64decode(encoded.encode("ascii"))
    encrypted_blob, encrypted_buffer = _blob_from_bytes(encrypted)
    plain_blob = DATA_BLOB()
    ok = unprotect(
        ctypes.byref(encrypted_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(plain_blob),
    )
    _ = encrypted_buffer
    if not ok:
        return ""
    return _bytes_from_blob(plain_blob, local_free).decode("utf-8")


@dataclass(slots=True)
class CiteAnythingConnection:
    id: str
    name: str
    base_url: str
    site: str = "custom"

    def public_dict(self, connected: bool) -> dict[str, Any]:
        return {**asdict(self), "connected": connected}


class ConnectionStore:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or syncanything_home()
        self.config_path = self.home / "connections.json"

    def list_citeanything(self) -> list[CiteAnythingConnection]:
        if not self.config_path.exists():
            return []
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        connections = payload.get("citeanything", []) if isinstance(payload, dict) else []
        result: list[CiteAnythingConnection] = []
        for item in connections:
            if not isinstance(item, dict):
                continue
            try:
                result.append(
                    CiteAnythingConnection(
                        id=str(item["id"]),
                        name=str(item["name"]),
                        base_url=str(item["base_url"]).rstrip("/"),
                        site=str(item.get("site") or "custom"),
                    )
                )
            except KeyError:
                continue
        return result

    def public_connections(self) -> list[dict[str, Any]]:
        return [
            connection.public_dict(bool(self.get_secret(connection.id)))
            for connection in self.list_citeanything()
        ]

    def add_citeanything(
        self, name: str, base_url: str, api_key: str, site: str = "custom"
    ) -> CiteAnythingConnection:
        name = name.strip()
        base_url = base_url.strip().rstrip("/")
        api_key = api_key.strip()
        if not name or not base_url.startswith(("https://", "http://")) or not api_key:
            raise ValueError("连接名称、站点地址和 API key 都不能为空")
        safe_name = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "account"
        connection = CiteAnythingConnection(
            id=f"{safe_name}-{uuid.uuid4().hex[:8]}",
            name=name,
            base_url=base_url,
            site=site,
        )
        self.set_secret(connection.id, api_key)
        connections = self.list_citeanything()
        connections.append(connection)
        self._write(connections)
        return connection

    def remove_citeanything(self, connection_id: str) -> bool:
        connections = self.list_citeanything()
        kept = [item for item in connections if item.id != connection_id]
        if len(kept) == len(connections):
            return False
        self.delete_secret(connection_id)
        self._write(kept)
        return True

    def get_secret(self, connection_id: str) -> str:
        if platform.system() == "Windows":
            path = _windows_secret_path(self.home, connection_id)
            try:
                return _unprotect_windows_secret(path.read_text(encoding="ascii").strip())
            except (OSError, ValueError, UnicodeDecodeError):
                return ""
        if platform.system() != "Darwin":
            return ""
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                connection_id,
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def set_secret(self, connection_id: str, api_key: str) -> None:
        if platform.system() == "Windows":
            path = _windows_secret_path(self.home, connection_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".dpapi.tmp")
            temporary.write_text(_protect_windows_secret(api_key) + "\n", encoding="ascii")
            temporary.replace(path)
            return
        if platform.system() != "Darwin":
            raise RuntimeError("当前版本仅支持在 macOS 钥匙串中保存连接密钥")
        result = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-a",
                connection_id,
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
                api_key,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "无法写入 macOS 钥匙串")

    def delete_secret(self, connection_id: str) -> None:
        if platform.system() == "Windows":
            try:
                _windows_secret_path(self.home, connection_id).unlink()
            except FileNotFoundError:
                pass
            return
        if platform.system() == "Darwin":
            subprocess.run(
                [
                    "security",
                    "delete-generic-password",
                    "-a",
                    connection_id,
                    "-s",
                    KEYCHAIN_SERVICE,
                ],
                capture_output=True,
                check=False,
            )

    def _write(self, connections: list[CiteAnythingConnection]) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "citeanything": [asdict(item) for item in connections]}
        temporary = self.config_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.config_path)
