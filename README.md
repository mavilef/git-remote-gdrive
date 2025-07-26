# git-gdrive-remote

A git remote helper that allows you to use Google Drive folders as Git repositories. This project implements the Git remote helper interface as described in the [Git documentation](https://git-scm.com/docs/gitremote-helpers).

## Installation

```bash
pip install git-gdrive-remote
```

## Configuration

Before using `git-gdrive-remote`, you need to set the `GDRIVE_CREDENTIALS_PATH` environment variable to the absolute path of your `credentials.json` file.

```bash
export GDRIVE_CREDENTIALS_PATH=/path/to/your/credentials.json
```
