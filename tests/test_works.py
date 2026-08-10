from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from syncanything.connections import CiteAnythingConnection
from syncanything.works import (
    METADATA_FILENAME,
    WorksSyncError,
    pull_work,
    push_work,
    select_checkout_client,
    select_works_client,
)


class FakeWorksClient:
    def __init__(self) -> None:
        self.connection = CiteAnythingConnection(
            id="international",
            name="International",
            base_url="https://citeanything.app",
            site="international",
        )
        self.uploaded: dict | None = None

    def list_works(self):
        return {
            "revision": "ws_one",
            "works": [
                {
                    "name": "article",
                    "path": "outputs/article",
                    "file_count": 2,
                    "files": [
                        {"path": "outputs/article/index.html"},
                        {"path": "outputs/article/style.css"},
                    ],
                }
            ],
        }

    def download_work(self, work_path, destination):
        assert work_path == "outputs/article"
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr("index.html", "<h1>Original</h1>")
            archive.writestr("style.css", "h1 { color: green; }")
        return "application/zip", "article.zip"

    def upload_work(self, work_path, expected_revision, archive_path, *, work_kind):
        with zipfile.ZipFile(archive_path) as archive:
            uploaded = {name: archive.read(name) for name in archive.namelist()}
        self.uploaded = {
            "work_path": work_path,
            "expected_revision": expected_revision,
            "work_kind": work_kind,
            "files": uploaded,
        }
        return {
            "work_path": work_path,
            "revision": "ws_two",
            "checkpointed_at": "2026-08-10T00:00:00Z",
            "file_count": len(uploaded),
            "total_bytes": sum(len(value) for value in uploaded.values()),
            "changed": True,
        }


class WorksTests(unittest.TestCase):
    def test_pull_edit_push_round_trip(self) -> None:
        client = FakeWorksClient()
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / "article"
            pulled = pull_work(client, "article", checkout)
            self.assertEqual(pulled["revision"], "ws_one")
            self.assertEqual((checkout / "index.html").read_text(), "<h1>Original</h1>")
            metadata = json.loads((checkout / METADATA_FILENAME).read_text())
            self.assertEqual(metadata["work_path"], "outputs/article")

            (checkout / "index.html").write_text("<h1>Edited by Codex</h1>")
            (checkout / ".git").mkdir()
            (checkout / ".git" / "config").write_text("do not upload")
            pushed = push_work(client, checkout)
            self.assertEqual(pushed["revision"], "ws_two")
            assert client.uploaded is not None
            self.assertEqual(client.uploaded["expected_revision"], "ws_one")
            self.assertEqual(
                client.uploaded["files"]["index.html"], b"<h1>Edited by Codex</h1>"
            )
            self.assertNotIn(METADATA_FILENAME, client.uploaded["files"])
            self.assertNotIn(".git/config", client.uploaded["files"])
            updated = json.loads((checkout / METADATA_FILENAME).read_text())
            self.assertEqual(updated["revision"], "ws_two")

    def test_single_file_work_round_trip_keeps_file_kind(self) -> None:
        class SingleFileClient(FakeWorksClient):
            def list_works(self):
                return {
                    "revision": "ws_file_one",
                    "works": [
                        {
                            "name": "notes.md",
                            "path": "outputs/notes.md",
                            "file_count": 1,
                            "files": [{"path": "outputs/notes.md"}],
                        }
                    ],
                }

            def download_work(self, work_path, destination):
                destination.write_bytes(b"old notes")
                return "text/markdown", "notes.md"

        client = SingleFileClient()
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / "notes"
            pull_work(client, "notes.md", checkout)
            metadata = json.loads((checkout / METADATA_FILENAME).read_text())
            self.assertEqual(metadata["work_kind"], "file")
            (checkout / "notes.md").write_text("edited notes")
            push_work(client, checkout)
            assert client.uploaded is not None
            self.assertEqual(client.uploaded["work_kind"], "file")
            self.assertEqual(client.uploaded["files"]["notes.md"], b"edited notes")

    def test_pull_refuses_existing_destination(self) -> None:
        client = FakeWorksClient()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "article"
            destination.mkdir()
            with self.assertRaises(WorksSyncError):
                pull_work(client, "article", destination)

    def test_multiple_connections_require_selector(self) -> None:
        one = FakeWorksClient()
        two = FakeWorksClient()
        two.connection = CiteAnythingConnection(
            id="china",
            name="China",
            base_url="https://citeanything.cn",
            site="china",
        )
        with self.assertRaises(WorksSyncError):
            select_works_client([one, two], None)
        self.assertIs(select_works_client([one, two], "china"), two)

        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            (checkout / METADATA_FILENAME).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "connection_id": "international",
                        "base_url": "https://citeanything.app",
                    }
                )
            )
            self.assertIs(select_checkout_client([one, two], checkout), one)


if __name__ == "__main__":
    unittest.main()
