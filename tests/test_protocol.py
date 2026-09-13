import io
import unittest
from unittest.mock import Mock

from git_remote_gdrive.protocol import RemoteHelperProtocol


class TestRemoteHelperProtocol(unittest.TestCase):
    def run_protocol(self, input_text, remote=None):
        remote = remote or Mock()
        output = io.StringIO()
        protocol = RemoteHelperProtocol(lambda: remote, io.StringIO(input_text), output)
        return protocol.run(), output.getvalue(), remote

    def test_capabilities_are_exact_and_do_not_construct_remote(self):
        factory = Mock()
        output = io.StringIO()
        protocol = RemoteHelperProtocol(factory, io.StringIO("capabilities\n"), output)

        self.assertEqual(protocol.run(), 0)
        self.assertEqual(output.getvalue(), "fetch\npush\noption\n\n")
        factory.assert_not_called()

    def test_list_is_terminated_by_blank_line(self):
        remote = Mock()
        remote.list_refs.return_value = [
            "@refs/heads/main HEAD",
            f"{'1' * 40} refs/heads/main",
        ]

        status, output, _ = self.run_protocol("list\n", remote)

        self.assertEqual(status, 0)
        self.assertEqual(
            output,
            f"@refs/heads/main HEAD\n{'1' * 40} refs/heads/main\n\n",
        )

    def test_options(self):
        status, output, _ = self.run_protocol(
            "option progress false\noption dry-run true\noption depth 1\n"
        )

        self.assertEqual(status, 0)
        self.assertEqual(output, "ok\nok\nunsupported\n")

    def test_push_status(self):
        remote = Mock()
        result = Mock(destination="refs/heads/main", ok=True, message=None)
        remote.push.return_value = [result]

        status, output, _ = self.run_protocol(
            "push refs/heads/main:refs/heads/main\n\n", remote
        )

        self.assertEqual(status, 0)
        self.assertEqual(output, "ok refs/heads/main\n\n")


if __name__ == "__main__":
    unittest.main()
