import io
import json
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import ANY, Mock, call, patch

from git_remote_gdrive.lfs_protocol import LFSTransferProtocol


OID = "a" * 64
OTHER_OID = "b" * 64


class TestLFSTransferProtocol(unittest.TestCase):
    def run_protocol(self, messages, factory=None, output=None):
        factory = factory if factory is not None else Mock()
        output = output if output is not None else io.StringIO()
        protocol = LFSTransferProtocol(
            factory,
            io.StringIO("".join(json.dumps(message) + "\n" for message in messages)),
            output,
        )
        status = protocol.run()
        return status, [json.loads(line) for line in output.getvalue().splitlines()], factory

    def test_init_and_terminate_do_not_construct_store_or_respond_to_terminate(self):
        status, output, factory = self.run_protocol(
            [
                {"event": "init", "remote": "drive", "operation": "upload"},
                {"event": "terminate"},
                {"event": "upload", "oid": OID, "size": 10, "path": "/unused"},
            ]
        )

        self.assertEqual(status, 0)
        self.assertEqual(output, [{}])
        factory.assert_not_called()

    def test_uploads_reuse_store_and_report_final_progress(self):
        status, output, factory = self.run_protocol(
            [
                {"event": "init", "remote": "drive", "operation": "upload"},
                {"event": "upload", "oid": OID, "size": 10, "path": "/first"},
                {"event": "upload", "oid": OTHER_OID, "size": 0, "path": "/empty"},
            ]
        )

        self.assertEqual(status, 0)
        factory.assert_called_once_with("drive", "upload")
        self.assertEqual(
            factory.return_value.upload.call_args_list,
            [
                call(OID, 10, Path("/first"), progress=ANY),
                call(OTHER_OID, 0, Path("/empty"), progress=ANY),
            ],
        )
        self.assertEqual(
            output,
            [
                {},
                {"event": "progress", "oid": OID, "bytesSoFar": 10, "bytesSinceLast": 10},
                {"event": "complete", "oid": OID},
                {"event": "progress", "oid": OTHER_OID, "bytesSoFar": 0, "bytesSinceLast": 0},
                {"event": "complete", "oid": OTHER_OID},
            ],
        )

    def test_download_returns_absolute_handoff_path(self):
        factory = Mock()
        factory.return_value.download.return_value = Path("downloaded-object")

        status, output, _ = self.run_protocol(
            [
                {"event": "init", "remote": "gd://folder", "operation": "download"},
                {"event": "download", "oid": OID, "size": 10, "action": None},
            ],
            factory,
        )

        self.assertEqual(status, 0)
        factory.assert_called_once_with("gd://folder", "download")
        factory.return_value.download.assert_called_once_with(OID, 10, progress=ANY)
        self.assertEqual(
            output[-1],
            {"event": "complete", "oid": OID, "path": str(Path("downloaded-object").resolve())},
        )

    def test_upload_progress_is_flushed_before_upload_returns_without_double_counting(self):
        factory = Mock()
        stream = io.StringIO()
        expected_progress = [
            {"event": "progress", "oid": OID, "bytesSoFar": 3, "bytesSinceLast": 3},
            {"event": "progress", "oid": OID, "bytesSoFar": 7, "bytesSinceLast": 4},
            {"event": "progress", "oid": OID, "bytesSoFar": 10, "bytesSinceLast": 3},
        ]

        def upload(oid, size, path, *, progress):
            for transferred, event_count in [(0, 0), (3, 1), (3, 1), (2, 1), (7, 2), (10, 3), (11, 3)]:
                progress(transferred)
                self.assertEqual(
                    [json.loads(line) for line in stream.getvalue().splitlines()],
                    [{}, *expected_progress[:event_count]],
                )
                self.assertEqual(flush.call_count, 1 + event_count)

        factory.return_value.upload.side_effect = upload
        with patch.object(stream, "flush", wraps=stream.flush) as flush:
            status, output, _ = self.run_protocol([
                {"event": "init", "remote": "drive", "operation": "upload"},
                {"event": "upload", "oid": OID, "size": 10, "path": "/source"},
            ], factory, stream)

        self.assertEqual(status, 0)
        self.assertEqual(output, [{}, *expected_progress, {"event": "complete", "oid": OID}])

    def test_download_progress_completes_only_remaining_bytes(self):
        factory = Mock()

        def download(oid, size, *, progress):
            progress(4)
            progress(7)
            return Path("downloaded-object")

        factory.return_value.download.side_effect = download
        status, output, _ = self.run_protocol([
            {"event": "init", "remote": "drive", "operation": "download"},
            {"event": "download", "oid": OID, "size": 10},
        ], factory)

        self.assertEqual(status, 0)
        self.assertEqual(output, [
            {},
            {"event": "progress", "oid": OID, "bytesSoFar": 4, "bytesSinceLast": 4},
            {"event": "progress", "oid": OID, "bytesSoFar": 7, "bytesSinceLast": 3},
            {"event": "progress", "oid": OID, "bytesSoFar": 10, "bytesSinceLast": 3},
            {"event": "complete", "oid": OID, "path": str(Path("downloaded-object").resolve())},
        ])

    def test_partial_failure_does_not_finish_progress_and_retry_restarts_counter(self):
        factory = Mock()

        def upload(oid, size, path, *, progress):
            if factory.return_value.upload.call_count == 1:
                progress(4)
                raise RuntimeError("connection lost")
            progress(2)
            progress(6)

        factory.return_value.upload.side_effect = upload
        request = {"event": "upload", "oid": OID, "size": 10, "path": "/source"}
        with self.assertLogs("git_remote_gdrive.lfs_protocol", level="ERROR"):
            status, output, _ = self.run_protocol([
                {"event": "init", "remote": "drive", "operation": "upload"},
                request,
                request,
            ], factory)

        self.assertEqual(status, 0)
        self.assertEqual(output, [
            {},
            {"event": "progress", "oid": OID, "bytesSoFar": 4, "bytesSinceLast": 4},
            {"event": "complete", "oid": OID, "error": {"code": 2, "message": "connection lost"}},
            {"event": "progress", "oid": OID, "bytesSoFar": 2, "bytesSinceLast": 2},
            {"event": "progress", "oid": OID, "bytesSoFar": 6, "bytesSinceLast": 4},
            {"event": "progress", "oid": OID, "bytesSoFar": 10, "bytesSinceLast": 4},
            {"event": "complete", "oid": OID},
        ])

    def test_object_error_does_not_stop_later_transfers(self):
        factory = Mock()
        factory.return_value.upload.side_effect = [RuntimeError("failed\ntry again"), None]

        with self.assertLogs("git_remote_gdrive.lfs_protocol", level="ERROR") as logs:
            status, output, _ = self.run_protocol(
                [
                    {"event": "init", "remote": "drive", "operation": "upload"},
                    {"event": "upload", "oid": OID, "size": 1, "path": "/first"},
                    {"event": "upload", "oid": OTHER_OID, "size": 1, "path": "/second"},
                ],
                factory,
            )

        self.assertEqual(status, 0)
        self.assertEqual(output[1], {
            "event": "complete", "oid": OID,
            "error": {"code": 2, "message": "failed\ntry again"},
        })
        self.assertEqual(output[-1], {"event": "complete", "oid": OTHER_OID})
        self.assertNotIn("Traceback", "\n".join(logs.output))

    def test_factory_failure_is_an_object_error_and_can_be_retried(self):
        store = Mock()
        factory = Mock(side_effect=[RuntimeError("authorization failed"), store])
        with self.assertLogs("git_remote_gdrive.lfs_protocol", level="ERROR"):
            status, output, _ = self.run_protocol(
                [
                    {"event": "init", "remote": "drive", "operation": "upload"},
                    {"event": "upload", "oid": OID, "size": 1, "path": "/first"},
                    {"event": "upload", "oid": OTHER_OID, "size": 1, "path": "/second"},
                ], factory,
            )

        self.assertEqual(status, 0)
        self.assertEqual(output[1]["error"]["message"], "authorization failed")
        self.assertEqual(output[-1], {"event": "complete", "oid": OTHER_OID})
        store.upload.assert_called_once_with(OTHER_OID, 1, Path("/second"), progress=ANY)

    def test_invalid_transfer_fields_never_reach_store(self):
        invalid_fields = [
            {"oid": "A" * 64}, {"oid": "a" * 63}, {"oid": "../object"}, {"oid": None},
            {"size": -1}, {"size": True}, {"size": 1.5}, {"size": "1"}, {"size": None},
            {"path": ""}, {"path": None}, {"event": "download"},
        ]
        for fields in invalid_fields:
            with self.subTest(fields=fields), self.assertLogs("git_remote_gdrive.lfs_protocol", level="ERROR"):
                request = {"event": "upload", "oid": OID, "size": 1, "path": "/source"}
                request.update(fields)
                status, output, factory = self.run_protocol([
                    {"event": "init", "remote": "drive", "operation": "upload"}, request,
                ])
                self.assertEqual(status, 0)
                self.assertEqual(output[-1]["event"], "complete")
                self.assertEqual(output[-1]["error"]["code"], 2)
                factory.assert_not_called()

    def test_invalid_init_returns_json_error_without_traceback(self):
        for message in [
            [], None, {}, {"event": "upload"},
            {"event": "init", "operation": "fetch", "remote": "drive"},
            {"event": "init", "operation": "upload", "remote": ""},
            {"event": "init", "operation": "upload", "remote": 1},
        ]:
            with self.subTest(message=message), self.assertLogs("git_remote_gdrive.lfs_protocol", level="ERROR") as logs:
                status, output, factory = self.run_protocol([message])
                self.assertEqual(status, 1)
                self.assertEqual(output[0]["error"]["code"], 1)
                self.assertNotIn("Traceback", "\n".join(logs.output))
                factory.assert_not_called()

    def test_malformed_json_is_fatal_and_init_errors_use_json(self):
        for initialized in (False, True):
            with self.subTest(initialized=initialized), self.assertLogs("git_remote_gdrive.lfs_protocol", level="ERROR"):
                prefix = '{"event":"init","operation":"upload","remote":"drive"}\n' if initialized else ""
                output = io.StringIO()
                protocol = LFSTransferProtocol(Mock(), io.StringIO(prefix + "bad json\n"), output)
                self.assertEqual(protocol.run(), 1)
                response = json.loads(output.getvalue())
                if initialized:
                    self.assertEqual(response, {})
                else:
                    self.assertEqual(response["error"]["message"], "invalid JSON in LFS request")

    def test_unknown_event_after_init_is_fatal(self):
        with self.assertLogs("git_remote_gdrive.lfs_protocol", level="ERROR"):
            status, output, factory = self.run_protocol([
                {"event": "init", "remote": "drive", "operation": "download"},
                {"event": "unknown"},
            ])
        self.assertEqual(status, 1)
        self.assertEqual(output, [{}])
        factory.assert_not_called()

    def test_backend_stdout_is_redirected_to_stderr(self):
        store = Mock()
        store.upload.side_effect = lambda *args, **kwargs: print("upload diagnostic")
        store.close.side_effect = lambda: print("cleanup diagnostic")

        def factory(*args):
            print("OAuth authorization URL")
            return store

        diagnostic = io.StringIO()
        with redirect_stderr(diagnostic):
            status, output, _ = self.run_protocol([
                {"event": "init", "remote": "drive", "operation": "upload"},
                {"event": "upload", "oid": OID, "size": 1, "path": "/source"},
            ], factory)
        self.assertEqual(status, 0)
        self.assertEqual(output[-1], {"event": "complete", "oid": OID})
        self.assertEqual(
            diagnostic.getvalue(),
            "OAuth authorization URL\nupload diagnostic\ncleanup diagnostic\n",
        )

    def test_store_is_closed_after_terminate_eof_or_fatal_error(self):
        for ending, expected_status in [
            ([], 0),
            ([{"event": "terminate"}], 0),
            ([{"event": "unknown"}], 1),
        ]:
            with self.subTest(ending=ending), patch("git_remote_gdrive.lfs_protocol.logger"):
                factory = Mock()
                factory.return_value.download.return_value = Path("handoff-object")
                status, _, _ = self.run_protocol([
                    {"event": "init", "remote": "drive", "operation": "download"},
                    {"event": "download", "oid": OID, "size": 1},
                    *ending,
                ], factory)
                self.assertEqual(status, expected_status)
                factory.return_value.close.assert_called_once_with()

    def test_cleanup_failure_does_not_change_transfer_result_or_print_traceback(self):
        factory = Mock()
        factory.return_value.close.side_effect = OSError("cannot remove temporary file")
        with self.assertLogs("git_remote_gdrive.lfs_protocol", level="WARNING") as logs:
            status, output, _ = self.run_protocol([
                {"event": "init", "remote": "drive", "operation": "upload"},
                {"event": "upload", "oid": OID, "size": 1, "path": "/source"},
            ], factory)
        self.assertEqual(status, 0)
        self.assertEqual(output[-1], {"event": "complete", "oid": OID})
        self.assertNotIn("Traceback", "\n".join(logs.output))

    def test_empty_input_exits_cleanly(self):
        status, output, factory = self.run_protocol([])
        self.assertEqual((status, output), (0, []))
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
