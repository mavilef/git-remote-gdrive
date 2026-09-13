import tempfile
import unittest
from pathlib import Path

from git_remote_gdrive.local_drive_client import LocalDriveClient


class TestLocalDriveClientProgress(unittest.TestCase):
    def test_upload_and_download_report_multiple_chunks(self):
        chunk_size = 8 * 1024 * 1024
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            client = LocalDriveClient(base / "drive")
            source = base / "source"
            with source.open("wb") as handle:
                handle.truncate(chunk_size + 1)
            upload_progress = []
            item = client.upload_path(".", "object", source, progress=upload_progress.append)
            download_progress = []
            destination = base / "download"
            client.download_to_path(item.id, destination, progress=download_progress.append)

            self.assertEqual(upload_progress, [chunk_size, chunk_size + 1])
            self.assertEqual(download_progress, upload_progress)
            self.assertEqual(destination.read_bytes(), source.read_bytes())

    def test_interrupted_upload_leaves_no_partial_remote_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            drive = base / "drive"
            client = LocalDriveClient(drive)
            source = base / "source"
            with source.open("wb") as handle:
                handle.truncate(8 * 1024 * 1024 + 1)

            def interrupt(_):
                raise RuntimeError("interrupted transfer")

            with self.assertRaisesRegex(RuntimeError, "interrupted transfer"):
                client.upload_path(".", "object", source, progress=interrupt)
            self.assertEqual(list(drive.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
