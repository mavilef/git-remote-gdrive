import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from git_remote_gdrive.errors import ConfigurationError
from git_remote_gdrive.google_drive_client_impl import GoogleDriveClientImpl


MODULE = "git_remote_gdrive.google_drive_client_impl"


class TestAuthentication(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.client = GoogleDriveClientImpl(
            base / "credentials.json", base / "state" / "token.json", service=Mock()
        )
        self.credentials = Mock(expired=False, valid=True, refresh_token="refresh")
        self.credentials.to_json.return_value = '{"token": "access"}'

    def test_authorization_message_goes_to_stderr(self):
        def authorize(**kwargs):
            self.assertEqual(kwargs, {"port": 0})
            print("Please visit https://example.test/authorize")
            return self.credentials

        stdout, stderr = StringIO(), StringIO()
        with patch(f"{MODULE}.InstalledAppFlow") as flow:
            flow.from_client_secrets_file.return_value.run_local_server.side_effect = (
                authorize
            )
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = self.client._authenticate()
                print("git protocol response")

        self.assertIs(result, self.credentials)
        self.assertEqual(stdout.getvalue(), "git protocol response\n")
        self.assertEqual(
            stderr.getvalue(), "Please visit https://example.test/authorize\n"
        )
        self.assertEqual(
            self.client.token_path.read_text(), self.credentials.to_json.return_value
        )

    def test_authorization_failure_restores_stdout(self):
        def authorize(**kwargs):
            print("Please visit https://example.test/authorize")
            raise RuntimeError("authorization cancelled")

        stdout, stderr = StringIO(), StringIO()
        with patch(f"{MODULE}.InstalledAppFlow") as flow:
            flow.from_client_secrets_file.return_value.run_local_server.side_effect = (
                authorize
            )
            with redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaisesRegex(ConfigurationError, "authorization cancelled"):
                    self.client._authenticate()
                print("git protocol response")

        self.assertEqual(stdout.getvalue(), "git protocol response\n")
        self.assertIn("https://example.test/authorize", stderr.getvalue())
        self.assertFalse(self.client.token_path.exists())

    def test_valid_token_does_not_start_authorization(self):
        self.client.token_path.parent.mkdir()
        self.client.token_path.write_text(self.credentials.to_json.return_value)
        with (
            patch(f"{MODULE}.Credentials.from_authorized_user_file", return_value=self.credentials),
            patch(f"{MODULE}.InstalledAppFlow") as flow,
        ):
            self.assertIs(self.client._authenticate(), self.credentials)

        self.credentials.refresh.assert_not_called()
        flow.from_client_secrets_file.assert_not_called()

    def test_expired_token_is_refreshed_and_saved(self):
        self.client.token_path.parent.mkdir()
        self.client.token_path.write_text('{"token": "expired"}')
        self.credentials.expired = True
        self.credentials.valid = False
        with (
            patch(f"{MODULE}.Credentials.from_authorized_user_file", return_value=self.credentials),
            patch(f"{MODULE}.InstalledAppFlow") as flow,
            patch(f"{MODULE}.Request") as request,
        ):
            self.assertIs(self.client._authenticate(), self.credentials)

        self.credentials.refresh.assert_called_once_with(request.return_value)
        flow.from_client_secrets_file.assert_not_called()
        self.assertEqual(
            self.client.token_path.read_text(), self.credentials.to_json.return_value
        )


if __name__ == "__main__":
    unittest.main()
