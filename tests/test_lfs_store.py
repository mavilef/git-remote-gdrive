import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from git_remote_gdrive.errors import DriveConflictError, DriveError
from git_remote_gdrive.lfs_store import LFSObjectStore
from git_remote_gdrive.local_drive_client import LocalDriveClient


class TestLFSObjectStore(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "drive" / "remote"
        self.root.mkdir(parents=True)
        self.client = LocalDriveClient(self.base / "drive")
        self.store = LFSObjectStore(
            self.client, "remote", cache_dir=self.base / "cache"
        )
        self.addCleanup(self.store.close)

    def source(self, content):
        oid = hashlib.sha256(content).hexdigest()
        source = self.base / oid
        source.write_bytes(content)
        return oid, source

    def remote_path(self, oid):
        return self.root / ".git-remote-gdrive" / "lfs" / "objects" / oid[:2] / oid

    def test_roundtrip_binary_and_empty_objects_with_cache_reuse(self):
        for content in (bytes(range(256)) * 8193, b""):
            with self.subTest(size=len(content)):
                oid, source = self.source(content)
                self.store.upload(oid, len(content), source)
                self.assertEqual(self.remote_path(oid).read_bytes(), content)
                downloaded = self.store.download(oid, len(content))
                self.assertEqual(downloaded.read_bytes(), content)
                with patch.object(
                    self.client, "download_to_path", side_effect=AssertionError("cache miss")
                ):
                    another = self.store.download(oid, len(content))
                    self.assertNotEqual(another, downloaded)
                    self.assertEqual(another.read_bytes(), content)
        self.assertFalse((self.root / ".git-remote-gdrive" / "manifest.json").exists())

    def test_upload_retry_verifies_remote_and_does_not_upload_again(self):
        content = b"retry me"
        oid, source = self.source(content)
        self.store.upload(oid, len(content), source)
        self.store.download(oid, len(content))
        with (
            patch.object(self.client, "upload_path") as upload,
            patch.object(
                self.client, "download_to_path", wraps=self.client.download_to_path
            ) as download,
        ):
            self.store.upload(oid, len(content), source)
        upload.assert_not_called()
        download.assert_called_once()

    def test_upload_with_lost_response_can_be_retried(self):
        content = b"committed despite lost response"
        oid, source = self.source(content)
        upload_path = self.client.upload_path

        def upload_then_fail(*args, **kwargs):
            upload_path(*args, **kwargs)
            raise DriveError("response lost")

        with patch.object(self.client, "upload_path", side_effect=upload_then_fail):
            with self.assertRaisesRegex(DriveError, "response lost"):
                self.store.upload(oid, len(content), source)
        self.assertEqual(self.remote_path(oid).read_bytes(), content)
        self.store.upload(oid, len(content), source)
        self.assertEqual(self.store.download(oid, len(content)).read_bytes(), content)

    def test_invalid_uploads_leave_remote_empty(self):
        content = b"valid bytes"
        oid, source = self.source(content)
        for bad_oid, bad_size in (
            ("../escape", len(content)),
            (oid.upper(), len(content)),
            ("a" * 63, len(content)),
            (None, len(content)),
            (oid, -1),
            (oid, True),
            (oid, float(len(content))),
            (oid, "11"),
            (oid, len(content) + 1),
            ("0" * 64, len(content)),
        ):
            with self.subTest(oid=bad_oid, size=bad_size):
                with self.assertRaises(DriveError):
                    self.store.upload(bad_oid, bad_size, source)
                self.assertEqual(list(self.root.iterdir()), [])
        with self.assertRaises(DriveError):
            self.store.upload(oid, len(content), self.base / "missing")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_download_metadata_rejected_even_with_cache(self):
        content = b"cached"
        oid, source = self.source(content)
        self.store.upload(oid, len(content), source)
        self.store.download(oid, len(content))
        for bad_oid, bad_size in (("../escape", len(content)), (oid, True), (oid, -1)):
            with self.subTest(oid=bad_oid, size=bad_size):
                with self.assertRaises(DriveError):
                    self.store.download(bad_oid, bad_size)

    def test_missing_download_does_not_create_remote_folders(self):
        with self.assertRaisesRegex(DriveError, "not found"):
            self.store.download("0" * 64, 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_corrupt_remote_is_rejected_and_temporary_file_removed(self):
        content = b"correct"
        oid, source = self.source(content)
        self.store.upload(oid, len(content), source)
        for corrupt in (b"wrong!!", b"truncated"):
            with self.subTest(content=corrupt):
                self.remote_path(oid).write_bytes(corrupt)
                with self.assertRaisesRegex(DriveError, "integrity verification"):
                    self.store.download(oid, len(content))
                self.assertFalse(any(p.is_file() for p in (self.base / "cache").rglob("*")))

    def test_duplicate_upload_rejects_corrupt_remote_despite_valid_cache(self):
        content = b"correct"
        oid, source = self.source(content)
        self.store.upload(oid, len(content), source)
        self.store.download(oid, len(content))
        self.remote_path(oid).write_bytes(b"wrong!!")
        with patch.object(self.client, "upload_path") as upload:
            with self.assertRaisesRegex(DriveError, "integrity verification"):
                self.store.upload(oid, len(content), source)
        upload.assert_not_called()
        self.assertEqual(self.remote_path(oid).read_bytes(), b"wrong!!")

    def test_corrupt_cache_is_replaced_from_remote(self):
        content = b"correct"
        oid, source = self.source(content)
        self.store.upload(oid, len(content), source)
        self.store.download(oid, len(content))
        cached = self.base / "cache" / "lfs" / "objects" / oid[:2] / oid
        cached.write_bytes(b"wrong!!")
        with patch.object(
            self.client, "download_to_path", wraps=self.client.download_to_path
        ) as download:
            downloaded = self.store.download(oid, len(content))
        download.assert_called_once()
        self.assertEqual(cached.read_bytes(), content)
        self.assertEqual(downloaded.read_bytes(), content)

    def test_moved_handoff_preserves_cache_and_close_cleans_unclaimed_files(self):
        content = b"move into Git LFS"
        oid, source = self.source(content)
        self.store.upload(oid, len(content), source)
        handoff = self.store.download(oid, len(content))
        handoff.rename(self.base / "git-lfs-object")
        with patch.object(self.client, "download_to_path") as download:
            unclaimed = self.store.download(oid, len(content))
        download.assert_not_called()
        self.store.close()
        self.assertFalse(unclaimed.exists())
        self.assertEqual((self.base / "git-lfs-object").read_bytes(), content)
        with patch.object(self.client, "download_to_path") as download:
            self.assertEqual(self.store.download(oid, len(content)).read_bytes(), content)
        download.assert_not_called()

    def test_interrupted_download_removes_temporary_file(self):
        content = b"correct"
        oid, source = self.source(content)
        self.store.upload(oid, len(content), source)

        def fail_download(file_id, destination):
            destination.write_bytes(b"partial")
            raise DriveError("download interrupted")

        with patch.object(self.client, "download_to_path", side_effect=fail_download):
            with self.assertRaisesRegex(DriveError, "download interrupted"):
                self.store.download(oid, len(content))
        self.assertFalse(any(p.is_file() for p in (self.base / "cache").rglob("*")))

    def test_remote_folder_conflicts_are_rejected(self):
        oid, source = self.source(b"object")
        (self.root / ".git-remote-gdrive").write_text("not a folder")
        with self.assertRaises(DriveConflictError):
            self.store.upload(oid, 6, source)
        with self.assertRaises(DriveConflictError):
            self.store.download(oid, 6)


if __name__ == "__main__":
    unittest.main()
