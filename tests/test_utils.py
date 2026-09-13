import unittest

from git_remote_gdrive.utils import get_folder_id_from_google_drive_url


class TestDriveFolderId(unittest.TestCase):
    def test_helper_urls(self):
        cases = {
            "gd://123abcXYZ-folder_id": "123abcXYZ-folder_id",
            "gdrive://another-valid-id_456": "another-valid-id_456",
            "googledrive://complex.ID-123": "complex.ID-123",
            "gd::shortId": "shortId",
            "gdrive::id_with_underscores": "id_with_underscores",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(get_folder_id_from_google_drive_url(value), expected)

    def test_raw_id_is_accepted_for_double_colon_helper_invocation(self):
        self.assertEqual(get_folder_id_from_google_drive_url("folder-123"), "folder-123")

    def test_google_drive_web_url(self):
        url = "https://drive.google.com/drive/folders/folder-123?usp=sharing"
        self.assertEqual(get_folder_id_from_google_drive_url(url), "folder-123")

    def test_invalid_values(self):
        for value in (
            "",
            "http://example.com/folder",
            "gd:",
            "gd:/folder",
            "gd:///folder",
            "folder with spaces",
            "../folder",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    get_folder_id_from_google_drive_url(value)


if __name__ == "__main__":
    unittest.main()
