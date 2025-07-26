import os
import tempfile
import logging

from git import Repo, GitCommandError

logger = logging.getLogger(__name__)

class GitRemote(object):
    def __init__(self, folder_id, client, repo=None):
        self.client = client
        self.folder_id = folder_id
        self.repo = repo if repo else Repo(os.getcwd()) # Initialize GitPython Repo object

    def list_refs(self) -> list[str]:
        logger.info("GitRemote.list_refs called.")
        all_refs = []

        head_files = self.client.list_files("refs/heads", self.folder_id)
        for file_name in head_files:
            full_ref_path = os.path.join("refs/heads", file_name)
            content = self.client.read_file_content(full_ref_path, self.folder_id)
            if content:
                sha = content.strip().decode("utf-8")
                all_refs.append(f"{sha} {full_ref_path}")

        tag_files = self.client.list_files("refs/tags", self.folder_id)
        for file_name in tag_files:
            full_ref_path = os.path.join("refs/tags", file_name)
            content = self.client.read_file_content(full_ref_path, self.folder_id)
            if content:
                sha = content.strip().decode("utf-8")
                all_refs.append(f"{sha} {full_ref_path}")

        logger.info(f"GitRemote.list_refs returning {len(all_refs)} refs.")
        return all_refs

    def get_capabilities(self) -> list[str]:
        logger.info("GitRemote.get_capabilities called.")
        return ["list", "fetch", "push"]

    def push(self, local_ref: str, remote_ref: str):
        logger.info(f"GitRemote.push called for local_ref={local_ref}, remote_ref={remote_ref}")
        # 1. Get local_src_sha using GitPython
        try:
            local_src_sha = self.repo.rev_parse(local_ref).hexsha
            logger.info(f"Resolved local_src_sha: {local_src_sha}")
        except GitCommandError as e:
            print(f"error {remote_ref} Failed to resolve local ref {local_ref}: {e.stderr.strip()}")
            logger.error(f"Failed to resolve local ref {local_ref}: {e.stderr.strip()}")
            return

        # 2. Get remote_dst_sha (if exists) using GoogleDriveClient
        remote_dst_sha = None
        try:
            logger.info(f"Attempting to read remote ref: {remote_ref}")
            # The read_file_content now takes a full path and root_folder_id
            remote_ref_content = self.client.read_file_content(remote_ref, self.folder_id)
            if remote_ref_content:
                remote_dst_sha = remote_ref_content.strip().decode("utf-8")
                logger.info(f"Resolved remote_dst_sha: {remote_dst_sha}")
            else:
                logger.info(f"Remote ref {remote_ref} does not exist on Google Drive.")
        except Exception as e:
            logger.warning(f"Error reading remote ref {remote_ref}: {e}")
            pass # Remote ref might not exist, which is fine for a new push

        # 3. Create Git Bundle
        temp_bundle_path = os.path.join(tempfile.gettempdir(), f"git_bundle_{os.urandom(8).hex()}.bundle")
        logger.info(f"Creating bundle at: {temp_bundle_path}")
        
        bundle_args = ["create", temp_bundle_path, local_src_sha]
        if remote_dst_sha:
            bundle_args.append(f"^{remote_dst_sha}")

        logger.info(f"Git bundle command arguments: {bundle_args}")
        try:
            self.repo.git.bundle(*bundle_args)
            logger.info("Bundle created successfully.")
        except GitCommandError as e:
            print(f"error {remote_ref} Failed to create bundle: {e.stderr.strip()}")
            logger.error(f"Failed to create bundle. Stderr: {e.stderr.strip()}")
            if os.path.exists(temp_bundle_path):
                os.remove(temp_bundle_path) # Clean up temp file
            return

        # 4. Upload the Bundle to Google Drive
        try:
            logger.info(f"Reading bundle content from: {temp_bundle_path}")
            with open(temp_bundle_path, "rb") as f:
                bundle_content = f.read()
            logger.info(f"Bundle content read. Size: {len(bundle_content)} bytes.")
            
            # We need a designated folder for bundles, e.g., 'bundles/'
            # This will require get_file_id_by_name and create_folder to be implemented
            # in GoogleDriveClientImpl.
            
            # Assuming the root folder_id is the parent for 'bundles'
            bundles_folder_id = self.client.get_file_id_by_name("bundles", self.folder_id)
            if not bundles_folder_id:
                logger.info(f"Bundles folder not found, creating it under {self.folder_id}.")
                bundles_folder_id = self.client.create_folder("bundles", self.folder_id)
                if not bundles_folder_id:
                    print(f"error {remote_ref} Failed to create bundles folder.")
                    logger.error(f"Failed to create bundles folder under {self.folder_id}.")
                    if os.path.exists(temp_bundle_path):
                        os.remove(temp_bundle_path)
                    return
                logger.info(f"Bundles folder created with ID: {bundles_folder_id}")

            bundle_name = os.path.basename(temp_bundle_path)
            logger.info(f"Uploading bundle '{bundle_name}' to bundles folder.")
            self.client.upload_file(bundle_name, bundle_content, "bundles", self.folder_id) # Assuming 'bundles' is the path
            logger.info("Bundle uploaded successfully.")

        except Exception as e:
            print(f"error {remote_ref} Failed to upload bundle: {e}")
            logger.error(f"Failed to upload bundle: {e}")
            if os.path.exists(temp_bundle_path):
                os.remove(temp_bundle_path) # Clean up temp file
            return
        finally:
            if os.path.exists(temp_bundle_path):
                os.remove(temp_bundle_path) # Ensure temp file is always removed
                logger.info(f"Cleaned up temporary bundle file: {temp_bundle_path}")

        # 5. Update Remote Ref on Google Drive
        try:
            ref_parent_path = os.path.dirname(remote_ref)
            ref_name = os.path.basename(remote_ref)
            logger.info(f"Updating remote ref '{remote_ref}' with SHA: {local_src_sha}")
            self.client.upload_file(ref_name, local_src_sha.encode("utf-8"), ref_parent_path, self.folder_id)
            print(f"ok {remote_ref}")
            logger.info(f"Successfully updated remote ref: {remote_ref}")
        except Exception as e:
            print(f"error {remote_ref} Failed to update remote ref: {e}")
            logger.error(f"Failed to update remote ref {remote_ref}: {e}")