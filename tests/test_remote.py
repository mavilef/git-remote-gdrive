import unittest
from unittest.mock import Mock
from src.git_remote import GitRemote

class TestGitRemote(unittest.TestCase):
    def test_list_refs(self):
        mock_client = Mock()

        # Mock the list_files to return the ref names
        mock_client.list_files.return_value = [
            "refs/heads/main",
            "refs/heads/develop",
            "refs/tags/v1.0"
        ]

        # Mock the read_file_content to return different SHAs for each ref
        def read_file_content_side_effect(path):
            if path == "refs/heads/main":
                return b"1111111111111111111111111111111111111111"
            elif path == "refs/heads/develop":
                return b"2222222222222222222222222222222222222222"
            elif path == "refs/tags/v1.0":
                return b"3333333333333333333333333333333333333333"
            return b""

        mock_client.read_file_content.side_effect = read_file_content_side_effect

        git_remote = GitRemote("fake_folder_id", mock_client)
        refs = git_remote.list_refs()

        self.assertEqual(refs, [
            "1111111111111111111111111111111111111111 refs/heads/main",
            "2222222222222222222222222222222222222222 refs/heads/develop",
            "3333333333333333333333333333333333333333 refs/tags/v1.0"
        ])

    def test_list_refs_empty(self):
        mock_client = Mock()
        mock_client.list_files.return_value = []
        git_remote = GitRemote("fake_folder_id", mock_client)
        refs = git_remote.list_refs()
        self.assertEqual(refs, [])

if __name__ == "__main__":
    unittest.main()