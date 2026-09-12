import json
import unittest

from git_remote_gdrive.errors import ManifestError
from git_remote_gdrive.manifest import BundleRecord, Manifest


class TestManifest(unittest.TestCase):
    def test_round_trip(self):
        manifest = Manifest(
            generation=3,
            head="refs/heads/main",
            refs={"refs/heads/main": "1" * 40},
            bundles=[
                BundleRecord(
                    file_id="file-id",
                    name="one.bundle",
                    sha256="2" * 64,
                    size=123,
                    created_at="2026-08-24T00:00:00+00:00",
                    tips=("1" * 40,),
                    prerequisites=(),
                )
            ],
        )

        restored = Manifest.from_bytes(manifest.to_bytes())

        self.assertEqual(restored, manifest)

    def test_rejects_unknown_format(self):
        value = json.loads(Manifest.empty().to_bytes())
        value["format_version"] = 99

        with self.assertRaisesRegex(ManifestError, "unsupported remote format"):
            Manifest.from_bytes(json.dumps(value).encode())

    def test_rejects_head_that_is_not_a_branch(self):
        value = json.loads(Manifest.empty().to_bytes())
        value["head"] = "refs/heads/main"

        with self.assertRaisesRegex(ManifestError, "HEAD"):
            Manifest.from_bytes(json.dumps(value).encode())

    def test_rejects_ref_that_could_inject_protocol_output(self):
        value = json.loads(Manifest.empty().to_bytes())
        value["refs"] = {"refs/heads/main\nfetch bad": "1" * 40}

        with self.assertRaisesRegex(ManifestError, "invalid remote ref"):
            Manifest.from_bytes(json.dumps(value).encode())


if __name__ == "__main__":
    unittest.main()
