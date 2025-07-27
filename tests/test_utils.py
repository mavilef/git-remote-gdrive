from src.utils import get_folder_id_from_google_drive_url
import unittest

class TestValidateGoogleDriveUrl(unittest.TestCase):

    def test_valid_gd_double_slash_url(self):
        """Test valid 'gd://FOLDER_ID' format."""
        url = "gd://123abcXYZ-folder_id"
        expected_folder_id = "123abcXYZ-folder_id"
        self.assertEqual(get_folder_id_from_google_drive_url(url), expected_folder_id)

    def test_valid_gdrive_double_slash_url(self):
        """Test valid 'gdrive://FOLDER_ID' format."""
        url = "gdrive://another-valid-id_456"
        expected_folder_id = "another-valid-id_456"
        self.assertEqual(get_folder_id_from_google_drive_url(url), expected_folder_id)

    def test_valid_googledrive_double_slash_url(self):
        """Test valid 'googledrive://FOLDER_ID' format."""
        url = "googledrive://complex.ID.with.dots-and-nums123"
        expected_folder_id = "complex.ID.with.dots-and-nums123"
        self.assertEqual(get_folder_id_from_google_drive_url(url), expected_folder_id)

    def test_valid_gd_double_colon_url(self):
        """Test valid 'gd::FOLDER_ID' format."""
        url = "gd::shortId"
        expected_folder_id = "shortId"
        self.assertEqual(get_folder_id_from_google_drive_url(url), expected_folder_id)

    def test_valid_gdrive_double_colon_url(self):
        """Test valid 'gdrive::FOLDER_ID' format."""
        url = "gdrive::id_with_underscores_and_hyphens-01"
        expected_folder_id = "id_with_underscores_and_hyphens-01"
        self.assertEqual(get_folder_id_from_google_drive_url(url), expected_folder_id)

    def test_valid_googledrive_double_colon_url(self):
        """Test valid 'googledrive::FOLDER_ID' format."""
        url = "googledrive::IDEndingWithNumber1"
        expected_folder_id = "IDEndingWithNumber1"
        self.assertEqual(get_folder_id_from_google_drive_url(url), expected_folder_id)

    def test_invalid_prefix(self):
        """Test URL with an invalid prefix."""
        url = "http://123abcXYZ"
        with self.assertRaisesRegex(ValueError, "Invalid Google Drive URL format"):
            get_folder_id_from_google_drive_url(url)

    def test_invalid_separator_single_colon(self):
        """Test URL with an invalid separator (single colon)."""
        url = "gd:123abcXYZ"
        with self.assertRaisesRegex(ValueError, "Invalid Google Drive URL format"):
            get_folder_id_from_google_drive_url(url)

    def test_invalid_separator_single_slash(self):
        """Test URL with an invalid separator (single slash)."""
        url = "gdrive:/123abcXYZ"
        with self.assertRaisesRegex(ValueError, "Invalid Google Drive URL format"):
            get_folder_id_from_google_drive_url(url)
            
    def test_invalid_separator_triple_slash(self):
        """Test URL with an invalid separator (triple slash)."""
        url = "gd:///123abcXYZ" # The regex `(?::://|::)` is specific.
        with self.assertRaisesRegex(ValueError, "Invalid Google Drive URL format"):
            get_folder_id_from_google_drive_url(url)

    def test_missing_folder_id_double_slash(self):
        """Test URL with '://' but no folder ID."""
        url = "gd://"
        with self.assertRaisesRegex(ValueError, "Invalid Google Drive URL format"):
            get_folder_id_from_google_drive_url(url)

    def test_missing_folder_id_double_colon(self):
        """Test URL with '::' but no folder ID."""
        url = "gdrive::"
        with self.assertRaisesRegex(ValueError, "Invalid Google Drive URL format"):
            get_folder_id_from_google_drive_url(url)

    def test_completely_unrelated_string(self):
        """Test a completely unrelated string."""
        url = "this_is_not_a_drive_url"
        with self.assertRaisesRegex(ValueError, "Invalid Google Drive URL format"):
            get_folder_id_from_google_drive_url(url)

    def test_empty_string(self):
        """Test an empty string as URL."""
        url = ""
        with self.assertRaisesRegex(ValueError, "Invalid Google Drive URL format"):
            get_folder_id_from_google_drive_url(url)

    def test_prefix_only(self):
        """Test URL with only the prefix."""
        url = "gd"
        with self.assertRaisesRegex(ValueError, "Invalid Google Drive URL format"):
            get_folder_id_from_google_drive_url(url)
        url = "googledrive"
        with self.assertRaisesRegex(ValueError, "Invalid Google Drive URL format"):
            get_folder_id_from_google_drive_url(url)
            
    def test_url_with_spaces_around_valid_core(self):
        """Test URL with leading/trailing spaces around a potentially valid core."""
        url = " gd::myfolder "
        with self.assertRaisesRegex(ValueError, "Invalid Google Drive URL format"):
            get_folder_id_from_google_drive_url(url)
