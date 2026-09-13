import json
import tempfile
import unittest
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpMockSequence

from git_remote_gdrive.errors import DriveConflictError, DriveError
from git_remote_gdrive.google_drive_client_impl import GoogleDriveClientImpl


FILE = {
    "id": "file-id",
    "name": "manifest.json",
    "mimeType": "application/json",
    "size": "12",
    "version": "3",
}


def client_with_responses(responses):
    http = HttpMockSequence(responses)
    service = build("drive", "v3", http=http, static_discovery=True)
    return GoogleDriveClientImpl("unused.json", service=service), http


class TestGoogleDriveClient(unittest.TestCase):
    def test_find_child_follows_an_empty_page(self):
        client, http = client_with_responses(
            [
                ({"status": "200"}, '{"files": [], "nextPageToken": "page-2"}'),
                ({"status": "200"}, json.dumps({"files": [FILE]})),
            ]
        )
        item = client.find_child("parent-id", "manifest.json")
        self.assertEqual((item.id, item.size, item.version), ("file-id", 12, "3"))
        self.assertEqual(len(http.request_sequence), 2)
        first_query = parse_qs(urlsplit(http.request_sequence[0][0]).query)
        second_query = parse_qs(urlsplit(http.request_sequence[1][0]).query)
        self.assertIn("nextPageToken", first_query["fields"][0])
        self.assertNotIn("pageToken", first_query)
        self.assertEqual(second_query["pageToken"], ["page-2"])
        self.assertEqual(
            second_query["q"],
            ["name = 'manifest.json' and 'parent-id' in parents and trashed = false"],
        )

    def test_find_child_rejects_duplicates_across_pages(self):
        client, http = client_with_responses(
            [
                (
                    {"status": "200"},
                    json.dumps({"files": [FILE], "nextPageToken": "page-2"}),
                ),
                (
                    {"status": "200"},
                    json.dumps({"files": [{**FILE, "id": "duplicate-id"}]}),
                ),
            ]
        )
        with self.assertRaisesRegex(DriveConflictError, "more than one file"):
            client.find_child("parent-id", "manifest.json")
        self.assertEqual(len(http.request_sequence), 2)

    def test_find_child_rejects_incomplete_search(self):
        client, http = client_with_responses(
            [({"status": "200"}, '{"files": [], "incompleteSearch": true}')]
        )
        with self.assertRaisesRegex(DriveError, "search was incomplete"):
            client.find_child("parent-id", "manifest.json")
        query = parse_qs(urlsplit(http.request_sequence[0][0]).query)
        self.assertIn("incompleteSearch", query["fields"][0])

    def test_upload_bytes_creates_or_updates_the_correct_file(self):
        content = b'{"generation": 1}\n'
        for existing_id, method, path in (
            (None, "POST", "/upload/drive/v3/files"),
            ("file-id", "PATCH", "/upload/drive/v3/files/file-id"),
        ):
            with self.subTest(existing_id=existing_id):
                client, http = client_with_responses(
                    [({"status": "200"}, json.dumps(FILE))]
                )
                item = client.upload_bytes(
                    "parent-id",
                    "manifest.json",
                    content,
                    mime_type="application/json",
                    existing_file_id=existing_id,
                )
                self.assertEqual(item.id, "file-id")
                uri, actual_method, body, headers = http.request_sequence[0]
                self.assertEqual(actual_method, method)
                self.assertEqual(urlsplit(uri).path, path)
                self.assertEqual(parse_qs(urlsplit(uri).query)["uploadType"], ["multipart"])
                message = BytesParser().parsebytes(
                    f"Content-Type: {headers['content-type']}\r\n\r\n".encode() + body
                )
                metadata, media = message.get_payload()
                expected_metadata = {"name": "manifest.json"}
                if existing_id is None:
                    expected_metadata["parents"] = ["parent-id"]
                self.assertEqual(json.loads(metadata.get_payload(decode=True)), expected_metadata)
                self.assertEqual(media.get_payload(decode=True), content)
                self.assertEqual(media.get_content_type(), "application/json")

    def test_upload_path_sends_the_bundle_through_a_resumable_session(self):
        session_url = "https://www.googleapis.com/upload/drive/v3/files?upload_id=session"
        client, http = client_with_responses(
            [
                ({"status": "200", "location": session_url}, ""),
                ({"status": "200"}, json.dumps(FILE)),
            ]
        )
        request = http.request

        def consume_stream(uri, method="GET", body=None, **kwargs):
            # Like a real HTTP transport, consume the stream before it is closed.
            if hasattr(body, "read"):
                body = body.read()
            return request(uri, method=method, body=body, **kwargs)

        content = b"# v2 git bundle\n\x00binary content\xff\n"
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "snapshot.bundle"
            source.write_bytes(content)
            with patch.object(http, "request", side_effect=consume_stream):
                client.upload_path("bundles-id", source.name, source)

        self.assertEqual(len(http.request_sequence), 2)
        uri, method, body, headers = http.request_sequence[0]
        self.assertEqual(method, "POST")
        self.assertEqual(parse_qs(urlsplit(uri).query)["uploadType"], ["resumable"])
        self.assertEqual(json.loads(body), {"name": "snapshot.bundle", "parents": ["bundles-id"]})
        self.assertEqual(headers["X-Upload-Content-Type"], "application/octet-stream")
        uri, method, body, headers = http.request_sequence[1]
        self.assertEqual((uri, method, body), (session_url, "PUT", content))
        self.assertEqual(headers["Content-Range"], f"bytes 0-{len(content) - 1}/{len(content)}")

    def test_download_reassembles_chunks_in_memory_and_on_disk(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "download.bundle"
            for to_path in (False, True):
                with self.subTest(to_path=to_path):
                    client, http = client_with_responses(
                        [
                            ({"status": "206", "content-range": "bytes 0-3/8"}, b"git\x00"),
                            ({"status": "206", "content-range": "bytes 4-7/8"}, b"data"),
                        ]
                    )
                    if to_path:
                        client.download_to_path("bundle-id", destination)
                        content = destination.read_bytes()
                    else:
                        content = client.download_bytes("bundle-id")
                    self.assertEqual(content, b"git\x00data")
                    self.assertEqual(len(http.request_sequence), 2)
                    for uri, method, _, _ in http.request_sequence:
                        self.assertEqual(method, "GET")
                        self.assertEqual(urlsplit(uri).path, "/drive/v3/files/bundle-id")
                        self.assertEqual(parse_qs(urlsplit(uri).query)["alt"], ["media"])

    def test_upload_reports_acknowledged_chunks_before_completion(self):
        chunk_size = 8 * 1024 * 1024
        size = chunk_size + 1
        session_url = "https://www.googleapis.com/upload/drive/v3/files?upload_id=session"
        client, http = client_with_responses(
            [
                ({"status": "200", "location": session_url}, ""),
                ({"status": "308", "range": f"bytes=0-{chunk_size - 1}"}, ""),
                ({"status": "200"}, json.dumps({**FILE, "size": str(size)})),
            ]
        )
        progress = []
        request = http.request

        def consume_stream(uri, method="GET", body=None, **kwargs):
            if hasattr(body, "read"):
                body = body.read()
            return request(uri, method=method, body=body, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "large-object"
            with source.open("wb") as handle:
                handle.truncate(size)
            with patch.object(http, "request", side_effect=consume_stream):
                item = client.upload_path(
                    "objects-id", source.name, source,
                    progress=lambda count: progress.append((count, len(http.request_sequence))),
                )

        self.assertEqual(item.size, size)
        self.assertEqual(progress, [(chunk_size, 2), (size, 3)])
        self.assertEqual(http.request_sequence[1][3]["Content-Range"], f"bytes 0-{chunk_size - 1}/{size}")
        self.assertEqual(http.request_sequence[2][3]["Content-Range"], f"bytes {chunk_size}-{chunk_size}/{size}")
        self.assertEqual(len(http.request_sequence[1][2]), chunk_size)
        self.assertEqual(http.request_sequence[2][2], b"\x00")

    def test_failed_upload_does_not_report_completion_or_retry(self):
        chunk_size = 8 * 1024 * 1024
        client, http = client_with_responses(
            [
                ({"status": "200", "location": "https://upload.example/session"}, ""),
                ({"status": "308", "range": f"bytes=0-{chunk_size - 1}"}, ""),
                ({"status": "503"}, '{"error": {"message": "unavailable"}}'),
            ]
        )
        progress = []
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "large-object"
            with source.open("wb") as handle:
                handle.truncate(chunk_size + 1)
            with self.assertRaises(DriveError):
                client.upload_path("objects-id", source.name, source, progress=progress.append)

        self.assertEqual(progress, [chunk_size])
        self.assertEqual(len(http.request_sequence), 3)

    def test_download_reports_chunks_before_completion(self):
        client, http = client_with_responses(
            [
                ({"status": "206", "content-range": "bytes 0-3/8"}, b"git\x00"),
                ({"status": "206", "content-range": "bytes 4-7/8"}, b"data"),
            ]
        )
        progress = []
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "object"
            client.download_to_path(
                "object-id", destination,
                progress=lambda count: progress.append((count, len(http.request_sequence))),
            )
            self.assertEqual(destination.read_bytes(), b"git\x00data")
        self.assertEqual(progress, [(4, 1), (8, 2)])
        self.assertEqual(http.request_sequence[0][3]["range"], f"bytes=0-{8 * 1024 * 1024 - 1}")

    def test_failed_download_does_not_report_completion(self):
        client, http = client_with_responses(
            [
                ({"status": "206", "content-range": "bytes 0-3/8"}, b"git\x00"),
                ({"status": "503"}, '{"error": {"message": "unavailable"}}'),
            ]
        )
        progress = []
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "object"
            with self.assertRaises(DriveError):
                client.download_to_path("object-id", destination, progress=progress.append)
        self.assertEqual(progress, [4])
        self.assertEqual(len(http.request_sequence), 2)

    def test_write_http_errors_are_translated_without_retries(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "snapshot.bundle"
            source.write_bytes(b"bundle")
            for operation in ("create_folder", "create_bytes", "update_bytes", "upload_path", "delete_file"):
                with self.subTest(operation=operation):
                    client, http = client_with_responses(
                        [({"status": "503"}, '{"error": {"message": "unavailable"}}')]
                    )
                    with self.assertRaises(DriveError) as raised:
                        if operation == "create_folder":
                            client.create_folder("parent-id", "folder")
                        elif operation in ("create_bytes", "update_bytes"):
                            client.upload_bytes(
                                "parent-id",
                                "manifest.json",
                                b"{}",
                                mime_type="application/json",
                                existing_file_id="file-id" if operation == "update_bytes" else None,
                            )
                        elif operation == "upload_path":
                            client.upload_path("parent-id", source.name, source)
                        else:
                            client.delete_file("file-id")
                    self.assertIsInstance(raised.exception.__cause__, HttpError)
                    self.assertEqual(len(http.request_sequence), 1)

    def test_read_http_errors_are_translated(self):
        for operation in ("find_child", "download_bytes"):
            with self.subTest(operation=operation):
                client, http = client_with_responses(
                    [({"status": "403"}, '{"error": {"message": "denied"}}')]
                )
                with self.assertRaises(DriveError) as raised:
                    if operation == "find_child":
                        client.find_child("parent-id", "manifest.json")
                    else:
                        client.download_bytes("file-id")
                self.assertIsInstance(raised.exception.__cause__, HttpError)
                self.assertEqual(len(http.request_sequence), 1)


if __name__ == "__main__":
    unittest.main()
