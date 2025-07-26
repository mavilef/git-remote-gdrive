import os

class GitRemote(object):
    def __init__(self, folder_id, client):
        self.client = client
        self.folder_id = folder_id

    def list_refs(self) -> list[str]:
        files = self.client.list_files(self.folder_id)
        refs = []
        for file in files:
            if file.startswith("refs/"):
                content = self.client.read_file_content(file)
                if content:
                    sha = content.strip().decode("utf-8")
                    refs.append(f"{sha} {file}")
        return refs

    def get_capabilities(self) -> list[str]:
        return ["list", "fetch", "push"]