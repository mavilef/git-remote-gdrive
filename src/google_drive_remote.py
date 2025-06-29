class GoogleDriveRemote(object):
    def __init__(self, folder_id):
        self._folder_id = folder_id

    def get_capabilities(self) -> list[str]:
        return []

