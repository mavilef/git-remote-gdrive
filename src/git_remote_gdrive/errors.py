class GitRemoteGDriveError(RuntimeError):
    """Base error for failures that can be shown to the Git client."""


class ConfigurationError(GitRemoteGDriveError):
    """The helper configuration is missing or invalid."""


class DriveError(GitRemoteGDriveError):
    """A Google Drive operation failed."""


class DriveConflictError(DriveError):
    """The remote changed while an update was being prepared."""


class ManifestError(GitRemoteGDriveError):
    """The remote manifest is invalid or unsupported."""


class GitCommandFailure(GitRemoteGDriveError):
    """A local Git command failed."""


class ProtocolError(GitRemoteGDriveError):
    """Git sent a malformed or unsupported helper command."""
