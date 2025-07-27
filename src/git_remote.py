import os
import tempfile
import logging

from git import Repo, GitCommandError

logger = logging.getLogger(__name__)

class GitRemote(object):
    def __init__(self, folder_id, client, repo=None):
        self.client = client
        self.folder_id = folder_id
        self.repo = repo if repo else Repo(os.getcwd()) 

    def list_refs(self) -> list[str]:
        pass
    def get_capabilities(self) -> list[str]:
        pass
    def push(self, local_ref: str, remote_ref: str):
        pass
