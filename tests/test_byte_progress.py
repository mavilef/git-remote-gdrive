import io
import unittest
from unittest.mock import patch

from git_remote_gdrive.byte_progress import ByteProgressReporter


class TerminalStream(io.StringIO):
    def isatty(self):
        return True


class TestByteProgressReporter(unittest.TestCase):
    def test_reports_file_bytes_with_spaces_without_object_percentage(self):
        stream = io.StringIO()
        reporter = ByteProgressReporter(stream)
        with patch.object(stream, "flush", wraps=stream.flush) as flush:
            reporter.update("upload 1/100 402653184/1073741824 folder/large asset.bin\n")
            flush.assert_called_once_with()
        self.assertEqual(
            stream.getvalue(),
            "LFS upload folder/large asset.bin: 37.5% (384.0 MiB / 1.0 GiB)\n",
        )

    def test_ignores_malformed_records(self):
        stream = io.StringIO()
        reporter = ByteProgressReporter(stream)
        for line in (
            "", "upload", "upload 1/1 1/10", "unknown 1/1 1/10 asset.bin",
            "upload -1/1 1/10 asset.bin",
            "upload 1/1 -1/10 asset.bin", "upload 1/1 11/10 asset.bin",
            "upload 1/1 0/-1 asset.bin", "upload 1/x 1/10 asset.bin",
            "upload 1/1 1.0/10 asset.bin", "upload 1/1 1/0 asset.bin",
            "upload 1/1/1 1/10 asset.bin", "upload 1/1 1/10/10 asset.bin",
        ):
            reporter.update(line)
        reporter.finish()
        self.assertEqual(stream.getvalue(), "")

    def test_suppresses_same_integer_percentage_but_reports_final_bytes(self):
        stream = io.StringIO()
        reporter = ByteProgressReporter(stream)
        for transferred in (100, 101, 109, 110, 999, 1000, 1000):
            reporter.update(f"upload 1/1 {transferred}/1000 asset.bin")
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 5)
        self.assertIn("10.0%", lines[0])
        self.assertIn("11.0%", lines[1])
        self.assertIn("99.9%", lines[2])
        self.assertIn("100.0%", lines[3])
        self.assertEqual(lines[3], lines[4])

    def test_retry_can_regress_within_same_percentage_after_suppressed_update(self):
        stream = io.StringIO()
        reporter = ByteProgressReporter(stream)
        for transferred in (501, 509, 505, 120, 501):
            reporter.update(f"upload 1/1 {transferred}/1000 asset.bin")
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertIn("50.5%", lines[1])
        self.assertIn("12.0%", lines[2])
        self.assertIn("50.1%", lines[3])

    def test_retry_file_index_can_exceed_estimated_file_count(self):
        stream = io.StringIO()
        reporter = ByteProgressReporter(stream)
        reporter.update("upload 1/1 50/100 asset.bin")
        reporter.update("upload 2/1 20/100 asset.bin")
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("50.0%", lines[0])
        self.assertIn("20.0%", lines[1])

    def test_file_and_direction_changes_report_even_at_same_percentage(self):
        stream = io.StringIO()
        reporter = ByteProgressReporter(stream)
        reporter.update("upload 1/2 50/100 one.bin")
        reporter.update("upload 2/2 50/100 two.bin")
        reporter.update("download 1/1 50/100 two.bin")
        reporter.update("checkout 1/1 50/100 two.bin")
        reporter.update("checkout 1/1 100/200 two.bin")
        self.assertEqual(len(stream.getvalue().splitlines()), 5)

    def test_sanitizes_control_characters_in_filename(self):
        stream = io.StringIO()
        reporter = ByteProgressReporter(stream)
        reporter.update("download 1/1 1/2 file\x1b[2J\r\x00\t\u202e.bin")
        self.assertEqual(
            stream.getvalue(),
            "LFS download file?[2J????.bin: 50.0% (1.0 B / 2.0 B)\n",
        )

    def test_zero_size_is_complete_only_when_explicitly_reported(self):
        stream = io.StringIO()
        reporter = ByteProgressReporter(stream)
        reporter.finish()
        self.assertEqual(stream.getvalue(), "")
        reporter.update("checkout 1/1 0/0 empty.bin")
        self.assertEqual(
            stream.getvalue(), "LFS checkout empty.bin: 100.0% (0.0 B / 0.0 B)\n",
        )

    def test_near_completion_does_not_round_up_to_100_percent(self):
        stream = io.StringIO()
        reporter = ByteProgressReporter(stream)
        reporter.update("upload 1/1 99999/100000 asset.bin")
        reporter.finish()
        self.assertIn("99.9%", stream.getvalue())
        self.assertNotIn("100.0%", stream.getvalue())

    def test_terminal_replaces_line_and_clears_shorter_tail_without_ansi(self):
        stream = TerminalStream()
        reporter = ByteProgressReporter(stream)
        reporter.update("upload 1/1 50/100 very long filename.bin")
        first = stream.getvalue()
        reporter.update("upload 1/1 60/100 x.bin")
        second = stream.getvalue()[len(first):]
        self.assertTrue(first.startswith("\rLFS upload"))
        self.assertTrue(second.startswith("\rLFS upload x.bin: 60.0%"))
        self.assertEqual(len(first), len(second))
        self.assertTrue(second.endswith(" " * (len("very long filename.bin") - len("x.bin"))))
        self.assertNotIn("\x1b", stream.getvalue())
        self.assertNotIn("\n", stream.getvalue())

    def test_finish_is_idempotent_and_never_fabricates_completion(self):
        for stream in (io.StringIO(), TerminalStream()):
            with self.subTest(tty=stream.isatty()):
                reporter = ByteProgressReporter(stream)
                reporter.update("upload 1/1 5/100 asset.bin")
                reporter.finish()
                finished = stream.getvalue()
                reporter.finish()
                self.assertEqual(stream.getvalue(), finished)
                self.assertEqual(finished.count("\n"), 1)
                self.assertNotIn("100.0%", finished)


if __name__ == "__main__":
    unittest.main()
