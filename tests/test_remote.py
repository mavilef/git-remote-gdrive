import sys
sys.path.insert(0, '/home/maviles/projects/git-remote-gdrive/src')

import unittest
from unittest.mock import Mock, patch, call, mock_open
from git_remote import GitRemote
from git import GitCommandError
import os
import tempfile

class TestGitRemote(unittest.TestCase):
    def test_list_refs(self):
        mock_client = Mock()
        fake_folder_id = "fake_folder_id"

        # Mock the list_files to return the ref names based on folder_path
        def list_files_side_effect(folder_path, root_folder_id):
            self.assertEqual(root_folder_id, fake_folder_id)
            if folder_path == "refs/heads":
                return ["main", "develop"]
            elif folder_path == "refs/tags":
                return ["v1.0"]
            return []

        mock_client.list_files.side_effect = list_files_side_effect

        # Mock the read_file_content to return different SHAs for each ref
        def read_file_content_side_effect(file_path, root_folder_id):
            self.assertEqual(root_folder_id, fake_folder_id)
            if file_path == "refs/heads/main":
                return b"1111111111111111111111111111111111111111\n"
            elif file_path == "refs/heads/develop":
                return b"2222222222222222222222222222222222222222\n"
            elif file_path == "refs/tags/v1.0":
                return b"3333333333333333333333333333333333333333\n"
            return b""

        mock_client.read_file_content.side_effect = read_file_content_side_effect

        git_remote = GitRemote(fake_folder_id, mock_client)
        refs = git_remote.list_refs()

        self.assertEqual(refs, [
            "1111111111111111111111111111111111111111 refs/heads/main",
            "2222222222222222222222222222222222222222 refs/heads/develop",
            "3333333333333333333333333333333333333333 refs/tags/v1.0"
        ])

    def test_list_refs_empty(self):
        mock_client = Mock()
        fake_folder_id = "fake_folder_id"

        # Mock list_files to return empty lists for both heads and tags
        def list_files_empty_side_effect(folder_path, root_folder_id):
            self.assertEqual(root_folder_id, fake_folder_id)
            return []
        mock_client.list_files.side_effect = list_files_empty_side_effect

        git_remote = GitRemote(fake_folder_id, mock_client)
        refs = git_remote.list_refs()
        self.assertEqual(refs, [])

    @patch('git.Repo')
    @patch('os.path.exists', return_value=True) # Mock os.path.exists for tempfile cleanup
    @patch('os.remove') # Mock os.remove for tempfile cleanup
    def test_push_new_branch(self, mock_os_remove, mock_os_path_exists, MockRepo):
        mock_client = Mock()
        fake_folder_id = "fake_folder_id"
        local_src_ref = "refs/heads/new-feature"
        remote_dst_ref = "refs/heads/new-feature"
        local_src_sha = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
        bundle_content = b"mock_bundle_content"

        # Mock GitPython Repo object
        mock_repo_instance = MockRepo.return_value
        # Mock rev_parse to return a mock object with hexsha
        mock_rev_parse_result = Mock()
        mock_rev_parse_result.hexsha = local_src_sha
        mock_repo_instance.rev_parse.return_value = mock_rev_parse_result

        # Mock git.bundle to do nothing, as GitRemote will handle file I/O
        mock_repo_instance.git.bundle.return_value = None

        # Mock GoogleDriveClient methods
        mock_client.read_file_content.return_value = b"" # Remote ref does not exist
        mock_client.get_file_id_by_name.side_effect = [None, "bundles_folder_id"] # First call for bundles folder, second for ref file
        mock_client.create_folder.return_value = "bundles_folder_id"
        mock_client.upload_file.return_value = "uploaded_file_id"

        # Mock open to simulate reading the bundle file
        m_open = mock_open(read_data=bundle_content)
        with patch('builtins.open', m_open):
            # Mock tempfile.gettempdir to control bundle path
            with patch('tempfile.gettempdir', return_value='/tmp'):
                git_remote = GitRemote(fake_folder_id, mock_client, repo=mock_repo_instance)
                git_remote.push(local_src_ref, remote_dst_ref)

        # Assert GitPython calls
        mock_repo_instance.rev_parse.assert_called_once_with(local_src_ref)
        # Capture the dynamically generated bundle path
        bundle_call_args, _ = mock_repo_instance.git.bundle.call_args
        generated_bundle_path = bundle_call_args[1] # The second argument to bundle is the path
        
        mock_repo_instance.git.bundle.assert_called_once_with(
            "create", generated_bundle_path, local_src_sha
        )

        # Assert GoogleDriveClient calls
        mock_client.read_file_content.assert_called_once_with(remote_dst_ref, fake_folder_id)
        mock_client.get_file_id_by_name.assert_has_calls([
            call("bundles", fake_folder_id),
        ])
        mock_client.create_folder.assert_called_once_with("bundles", fake_folder_id)
        mock_client.upload_file.assert_has_calls([
            call(os.path.basename(generated_bundle_path), bundle_content, "bundles", fake_folder_id),
            call(os.path.basename(remote_dst_ref), local_src_sha.encode("utf-8"), os.path.dirname(remote_dst_ref), fake_folder_id)
        ])

        # Assert temp file cleanup
        mock_os_remove.assert_called_once_with(generated_bundle_path)

if __name__ == "__main__":
    unittest.main()
