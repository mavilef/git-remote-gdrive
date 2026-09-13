import tempfile
import unittest
from pathlib import Path

from git_remote_gdrive.errors import DriveConflictError, DriveError
from git_remote_gdrive.local_drive_client import LocalDriveClient
from git_remote_gdrive.manifest import BundleRecord, Manifest
from git_remote_gdrive.store import DriveRemoteStore, sha256_file


class TestDriveRemoteStore(unittest.TestCase):
    def test_download_reports_bytes_and_skips_cached_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "drive" / "remote").mkdir(parents=True)
            store = DriveRemoteStore(
                LocalDriveClient(base / "drive"), "remote", cache_dir=base / "cache",
            )
            source = base / "source.bundle"
            source.write_bytes(b"x" * (9 * 1024 * 1024))
            item = store.upload_bundle("history.bundle", source)
            bundle = BundleRecord(
                item.id, item.name, sha256_file(source), source.stat().st_size, "now",
            )
            updates = []

            downloaded = store.cached_bundle(bundle, progress=updates.append)

            self.assertEqual(updates, [0, 8 * 1024 * 1024, bundle.size])
            self.assertEqual(sha256_file(downloaded), bundle.sha256)
            updates.clear()
            self.assertEqual(store.cached_bundle(bundle, progress=updates.append), downloaded)
            self.assertEqual(updates, [])

    def test_corrupt_download_does_not_report_completion(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "drive" / "remote").mkdir(parents=True)
            store = DriveRemoteStore(
                LocalDriveClient(base / "drive"), "remote", cache_dir=base / "cache",
            )
            source = base / "source.bundle"
            source.write_bytes(b"x" * (9 * 1024 * 1024))
            item = store.upload_bundle("history.bundle", source)
            bundle = BundleRecord(
                item.id, item.name, "0" * 64, source.stat().st_size, "now",
            )
            updates = []

            with self.assertRaisesRegex(DriveError, "integrity verification"):
                store.cached_bundle(bundle, progress=updates.append)

            self.assertEqual(updates, [0, 8 * 1024 * 1024])
            self.assertEqual(list((base / "cache").rglob("*.bundle")), [])

    def test_concurrent_manifest_update_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "drive" / "remote").mkdir(parents=True)
            store = DriveRemoteStore(
                LocalDriveClient(base / "drive"),
                "remote",
                cache_dir=base / "cache",
            )
            first_reader = store.load_manifest()
            second_reader = store.load_manifest()

            first_update = Manifest.empty()
            first_update.generation = 1
            store.save_manifest(first_reader, first_update)

            second_update = Manifest.empty()
            second_update.generation = 1
            with self.assertRaisesRegex(DriveConflictError, "remote changed"):
                store.save_manifest(second_reader, second_update)


if __name__ == "__main__":
    unittest.main()
