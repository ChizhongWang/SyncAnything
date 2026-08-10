"""Bidirectional local checkouts for CiteAnything durable works."""

from __future__ import annotations

import http.client
import json
import os
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from syncanything.connections import CiteAnythingConnection
from syncanything.sources.citeanything import CiteAnythingAdapter


METADATA_FILENAME = ".citeanything-work.json"
USER_AGENT = "SyncAnything/0.4"


class WorksSyncError(RuntimeError):
    pass


class WorksConflictError(WorksSyncError):
    def __init__(self, message: str, current_revision: str = "") -> None:
        super().__init__(message)
        self.current_revision = current_revision


class CiteAnythingWorksClient:
    def __init__(self, connection: CiteAnythingConnection, api_key: str) -> None:
        self.connection = connection
        self.api_key = api_key

    def list_works(self) -> dict[str, Any]:
        return self._request_json("/api/machines/default/works")

    def download_work(self, work_path: str, destination: Path) -> tuple[str, str]:
        query = urlencode({"path": work_path, "download": "true"})
        request = self._request(f"/api/machines/default/works/content?{query}")
        try:
            with urlopen(request, timeout=120) as response, destination.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
                return (
                    response.headers.get_content_type(),
                    response.headers.get_filename() or "",
                )
        except HTTPError as error:
            self._raise_http_error(error)
        except (URLError, TimeoutError, OSError) as error:
            raise WorksSyncError(f"Could not download work: {error}") from error
        raise WorksSyncError("Could not download work")

    def upload_work(
        self,
        work_path: str,
        expected_revision: str,
        archive_path: Path,
        *,
        work_kind: str,
    ) -> dict[str, Any]:
        boundary = f"----SyncAnything{uuid.uuid4().hex}"
        prefix = _multipart_fields(
            boundary,
            {
                "work_path": work_path,
                "expected_revision": expected_revision,
                "work_kind": work_kind,
            },
        )
        suffix = f"\r\n--{boundary}--\r\n".encode()
        file_header = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="archive"; filename="work.zip"\r\n'
            "Content-Type: application/zip\r\n\r\n"
        ).encode()
        content_length = len(prefix) + len(file_header) + archive_path.stat().st_size + len(suffix)
        parsed = urlparse(self.connection.base_url)
        connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(parsed.hostname, parsed.port, timeout=180)
        path_prefix = parsed.path.rstrip("/")
        try:
            connection.putrequest("POST", f"{path_prefix}/api/machines/default/works/import")
            connection.putheader("Authorization", f"Bearer {self.api_key}")
            connection.putheader("Accept", "application/json")
            connection.putheader("User-Agent", USER_AGENT)
            connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            connection.putheader("Content-Length", str(content_length))
            connection.endheaders()
            connection.send(prefix)
            connection.send(file_header)
            with archive_path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    connection.send(chunk)
            connection.send(suffix)
            response = connection.getresponse()
            body = response.read()
            if response.status >= 400:
                self._raise_response_error(response.status, body)
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise WorksSyncError("CiteAnything returned an invalid upload response") from error
            if not isinstance(payload, dict):
                raise WorksSyncError("CiteAnything returned an invalid upload response")
            return payload
        except (OSError, TimeoutError, http.client.HTTPException) as error:
            raise WorksSyncError(f"Could not upload work: {error}") from error
        finally:
            connection.close()

    def _request_json(self, path: str) -> dict[str, Any]:
        try:
            with urlopen(self._request(path), timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            self._raise_http_error(error)
        except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorksSyncError(f"Could not read works: {error}") from error
        if not isinstance(payload, dict):
            raise WorksSyncError("CiteAnything returned an invalid works response")
        return payload

    def _request(self, path: str) -> Request:
        return Request(
            f"{self.connection.base_url.rstrip('/')}{path}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": USER_AGENT,
            },
        )

    def _raise_http_error(self, error: HTTPError) -> None:
        body = error.read()
        self._raise_response_error(error.code, body)

    @staticmethod
    def _raise_response_error(status: int, body: bytes) -> None:
        message = f"CiteAnything request failed ({status})"
        current_revision = ""
        try:
            payload = json.loads(body.decode("utf-8"))
            detail = payload.get("detail") if isinstance(payload, dict) else None
            if isinstance(detail, dict):
                message = str(detail.get("message") or message)
                current_revision = str(detail.get("current_revision") or "")
            elif isinstance(detail, str):
                message = detail
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        if status == 409:
            raise WorksConflictError(message, current_revision)
        if status == 403 and "scope" in message.lower():
            message += "; generate a new SyncAnything key with works.read and works.write"
        raise WorksSyncError(message)


def configured_works_clients() -> list[CiteAnythingWorksClient]:
    adapter = CiteAnythingAdapter()
    return [
        CiteAnythingWorksClient(connection, secret)
        for connection, secret in adapter.connections
        if secret
    ]


def select_works_client(
    clients: list[CiteAnythingWorksClient], selector: str | None
) -> CiteAnythingWorksClient:
    if selector:
        normalized = selector.strip().casefold()
        matches = [
            client
            for client in clients
            if normalized
            in {
                client.connection.id.casefold(),
                client.connection.name.casefold(),
                client.connection.site.casefold(),
                client.connection.base_url.casefold(),
            }
        ]
        if len(matches) == 1:
            return matches[0]
        raise WorksSyncError(f"No unique CiteAnything connection matches: {selector}")
    if len(clients) == 1:
        return clients[0]
    if not clients:
        raise WorksSyncError("No CiteAnything connection is configured")
    names = ", ".join(client.connection.id for client in clients)
    raise WorksSyncError(f"Multiple CiteAnything connections exist; choose --connection from: {names}")


def select_checkout_client(
    clients: list[CiteAnythingWorksClient],
    directory: Path,
    selector: str | None = None,
) -> CiteAnythingWorksClient:
    if selector:
        return select_works_client(clients, selector)
    metadata_path = directory.expanduser().resolve() / METADATA_FILENAME
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return select_works_client(clients, None)
    connection_id = str(metadata.get("connection_id") or "") if isinstance(metadata, dict) else ""
    base_url = str(metadata.get("base_url") or "").rstrip("/") if isinstance(metadata, dict) else ""
    matches = [
        client
        for client in clients
        if client.connection.id == connection_id
        or client.connection.base_url.rstrip("/") == base_url
    ]
    if len(matches) == 1:
        return matches[0]
    return select_works_client(clients, None)


def pull_work(
    client: CiteAnythingWorksClient,
    identifier: str,
    destination: Path | None = None,
) -> dict[str, Any]:
    listing = client.list_works()
    works = listing.get("works") if isinstance(listing, dict) else None
    if not isinstance(works, list):
        raise WorksSyncError("CiteAnything returned an invalid works list")
    matches = [
        work
        for work in works
        if isinstance(work, dict)
        and identifier in {str(work.get("path") or ""), str(work.get("name") or "")}
    ]
    if len(matches) != 1:
        raise WorksSyncError(f"No unique work matches: {identifier}")
    work = matches[0]
    work_name = str(work.get("name") or "work")
    target = (destination or Path(work_name)).expanduser().resolve()
    if target.exists():
        raise WorksSyncError(f"Destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    descriptor, archive_name = tempfile.mkstemp(prefix="syncanything-work-", suffix=".download")
    os.close(descriptor)
    archive_path = Path(archive_name)
    temp_directory = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        content_type, filename = client.download_work(str(work["path"]), archive_path)
        is_directory = any(
            isinstance(item, dict) and str(item.get("path") or "") != str(work["path"])
            for item in work.get("files", [])
        )
        if is_directory or content_type in {"application/zip", "application/x-zip-compressed"}:
            _safe_extract_zip(archive_path, temp_directory)
        else:
            output_name = filename or Path(str(work["path"])).name
            shutil.copyfile(archive_path, temp_directory / output_name)

        metadata = {
            "version": 1,
            "base_url": client.connection.base_url,
            "connection_id": client.connection.id,
            "work_path": str(work["path"]),
            "work_kind": "directory" if is_directory else "file",
            "revision": str(listing.get("revision") or ""),
            "pulled_at": datetime.now(timezone.utc).isoformat(),
        }
        (temp_directory / METADATA_FILENAME).write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_directory, target)
        return {**metadata, "local_path": str(target)}
    finally:
        archive_path.unlink(missing_ok=True)
        if temp_directory.exists():
            shutil.rmtree(temp_directory)


def push_work(
    client: CiteAnythingWorksClient,
    directory: Path,
) -> dict[str, Any]:
    root = directory.expanduser().resolve()
    metadata_path = root / METADATA_FILENAME
    if not root.is_dir() or not metadata_path.is_file():
        raise WorksSyncError(f"Not a pulled CiteAnything work: {root}")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorksSyncError("Local work metadata is invalid") from error
    if not isinstance(metadata, dict) or int(metadata.get("version", 0)) != 1:
        raise WorksSyncError("Local work metadata is unsupported")
    if str(metadata.get("base_url") or "").rstrip("/") != client.connection.base_url.rstrip("/"):
        raise WorksSyncError("This checkout belongs to a different CiteAnything connection")
    metadata["connection_id"] = client.connection.id

    descriptor, archive_name = tempfile.mkstemp(prefix="syncanything-work-", suffix=".zip")
    os.close(descriptor)
    archive_path = Path(archive_name)
    try:
        file_count = _write_work_archive(root, archive_path)
        if file_count == 0:
            raise WorksSyncError("Local work contains no files")
        response = client.upload_work(
            str(metadata.get("work_path") or ""),
            str(metadata.get("revision") or ""),
            archive_path,
            work_kind=str(metadata.get("work_kind") or "directory"),
        )
        metadata["revision"] = str(response.get("revision") or metadata.get("revision") or "")
        metadata["pushed_at"] = datetime.now(timezone.utc).isoformat()
        temporary = metadata_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(metadata_path)
        return {
            **response,
            "local_path": str(root),
            "connection_id": client.connection.id,
        }
    finally:
        archive_path.unlink(missing_ok=True)


def _write_work_archive(root: Path, archive_path: Path) -> int:
    count = 0
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if METADATA_FILENAME in relative.parts or ".git" in relative.parts:
                continue
            if path.is_symlink():
                raise WorksSyncError(f"Symbolic links are not supported: {relative}")
            if path.is_file():
                archive.write(path, arcname=relative.as_posix())
                count += 1
    return count


def _safe_extract_zip(archive_path: Path, destination: Path) -> None:
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as error:
        raise WorksSyncError("CiteAnything returned an invalid work archive") from error
    with archive:
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            mode = (info.external_attr >> 16) & 0o177777
            if (
                path.is_absolute()
                or not path.parts
                or any(part in {"", ".", ".."} for part in path.parts)
                or mode & 0o170000 == 0o120000
            ):
                raise WorksSyncError("Work archive contains an unsafe path")
            output = destination.joinpath(*path.parts)
            if info.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, output.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)


def _multipart_fields(boundary: str, fields: dict[str, str]) -> bytes:
    chunks = []
    for name, value in fields.items():
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        )
    return "".join(chunks).encode()
