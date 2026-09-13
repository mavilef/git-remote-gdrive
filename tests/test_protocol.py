import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from git_remote_gdrive.errors import ConfigurationError, DriveError
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

    def test_push_preparation_runs_before_advertising_refs_only_for_push(self):
        for command in ("list", "list for-push"):
            with self.subTest(command=command):
                output = io.StringIO()
                remote = Mock()
                remote.list_refs.return_value = []
                prepare = Mock(side_effect=lambda: self.assertEqual(output.getvalue(), ""))
                protocol = RemoteHelperProtocol(
                    lambda: remote, io.StringIO(command + "\n"), output,
                    prepare_push=prepare,
                )

                self.assertEqual(protocol.run(), 0)
                self.assertEqual(prepare.call_count, int(command == "list for-push"))
                self.assertEqual(output.getvalue(), "\n")

    def test_setup_conflict_stops_push_before_contacting_drive(self):
        factory = Mock()
        output = io.StringIO()
        protocol = RemoteHelperProtocol(
            factory, io.StringIO("list for-push\n"), output,
            prepare_push=Mock(side_effect=ConfigurationError("custom hook conflict")),
        )

        with self.assertLogs("git_remote_gdrive.protocol", level="ERROR"):
            self.assertEqual(protocol.run(), 1)
        factory.assert_not_called()
        self.assertEqual(output.getvalue(), "")

    def test_fetch_prepares_lfs_after_objects_arrive_before_checkout_ack(self):
        remote = Mock()
        output = io.StringIO()
        object_id = "1" * 40

        def prepare(object_ids):
            remote.fetch.assert_called_once()
            self.assertEqual(object_ids, [object_id])
            self.assertEqual(output.getvalue(), "ok\nok\n")

        protocol = RemoteHelperProtocol(
            lambda: remote,
            io.StringIO(
                "option cloning true\noption check-connectivity true\n"
                f"fetch {object_id} refs/heads/main\n\n"
            ),
            output, prepare_fetch=prepare,
        )

        self.assertEqual(protocol.run(), 0)
        self.assertEqual(output.getvalue(), "ok\nok\nconnectivity-ok\n\n")

    def test_failed_fetch_setup_does_not_acknowledge_success(self):
        output = io.StringIO()
        protocol = RemoteHelperProtocol(
            Mock(), io.StringIO(f"option cloning true\nfetch {'1' * 40} refs/heads/main\n\n"), output,
            prepare_fetch=Mock(side_effect=ConfigurationError("LFS endpoint conflict")),
        )

        with self.assertLogs("git_remote_gdrive.protocol", level="ERROR"):
            self.assertEqual(protocol.run(), 1)
        self.assertEqual(output.getvalue(), "ok\n")

    def test_normal_fetch_does_not_prepare_clone_configuration(self):
        prepare = Mock()
        output = io.StringIO()
        protocol = RemoteHelperProtocol(
            Mock(), io.StringIO(f"fetch {'1' * 40} refs/heads/main\n\n"), output,
            prepare_fetch=prepare,
        )

        self.assertEqual(protocol.run(), 0)
        prepare.assert_not_called()
        self.assertEqual(output.getvalue(), "\n")

    def test_clone_progress_is_on_stderr_and_honors_options(self):
        for options, visible in (
            ("option cloning true\n", True),
            ("option cloning true\noption progress false\n", False),
            ("option cloning true\noption verbosity 0\noption progress false\n", False),
            ("option cloning true\noption verbosity 0\noption progress true\n", True),
            ("", False),
        ):
            with self.subTest(options=options):
                remote = Mock()
                output, errors = io.StringIO(), io.StringIO()

                def fetch(requests, *, check_connectivity, progress):
                    self.assertEqual(progress is not None, visible)
                    if progress is not None:
                        progress(SimpleNamespace(name="history.bundle", size=8), 3)

                remote.fetch.side_effect = fetch
                protocol = RemoteHelperProtocol(
                    lambda: remote,
                    io.StringIO(options + f"fetch {'1' * 40} refs/heads/main\n\n"),
                    output, stderr=errors,
                )

                self.assertEqual(protocol.run(), 0)
                self.assertEqual(output.getvalue(), "ok\n" * options.count("\n") + "\n")
                self.assertEqual(
                    errors.getvalue(),
                    "Drive download history.bundle: 37.5% (3.0 B / 8.0 B)\n" if visible else "",
                )

    def test_failed_clone_finishes_terminal_progress_without_completion_or_ack(self):
        class TerminalStream(io.StringIO):
            def isatty(self):
                return True

        remote = Mock()
        output, errors = io.StringIO(), TerminalStream()

        def fetch(requests, *, check_connectivity, progress):
            progress(SimpleNamespace(name="history.bundle", size=8), 3)
            raise DriveError("download interrupted")

        remote.fetch.side_effect = fetch
        protocol = RemoteHelperProtocol(
            lambda: remote,
            io.StringIO(f"option cloning true\nfetch {'1' * 40} refs/heads/main\n\n"),
            output, stderr=errors,
        )
        with self.assertLogs("git_remote_gdrive.protocol", level="ERROR"):
            self.assertEqual(protocol.run(), 1)
        self.assertEqual(output.getvalue(), "ok\n")
        self.assertEqual(
            errors.getvalue(), "\rDrive download history.bundle: 37.5% (3.0 B / 8.0 B)\n",
        )

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
