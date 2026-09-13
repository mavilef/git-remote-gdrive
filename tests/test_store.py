import tempfile
import unittest
from pathlib import Path

from git_remote_gdrive.errors import DriveConflictError
from git_remote_gdrive.local_drive_client import LocalDriveClient
from git_remote_gdrive.manifest import Manifest
from git_remote_gdrive.store import DriveRemoteStore


class TestDriveRemoteStore(unittest.TestCase):
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
