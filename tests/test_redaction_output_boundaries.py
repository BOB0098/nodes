#!/usr/bin/env python3
"""P24 C6 回归：外部 URL 及嵌入凭据不能进入日志或公开报告。"""
import io
import sys
import tempfile
import warnings
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import discover  # noqa: E402
import netsafe  # noqa: E402
import screen  # noqa: E402


class RedactionOutputBoundariesTest(unittest.TestCase):
    def test_discover_dry_run_masks_candidate_url_in_stderr(self):
        token = "ghp_abcdefghijklmnopqrstuvwxyz012345"
        candidate = {
            "full_name": "owner/repo",
            "stargazers_count": 10,
            "default_branch": "main",
        }
        file_info = {"path": f"sub/{token}", "size": 4096, "format": "clash"}
        stderr = io.StringIO()
        with (
            patch.object(discover.sys, "argv", ["discover.py", "--dry-run"]),
            patch.object(discover, "QUERIES", ["fixture"]),
            patch.object(discover, "known_repos", return_value=set()),
            patch.object(discover, "api", return_value=({"total_count": 1, "items": [candidate]}, {})),
            patch.object(discover, "pick_file", return_value=file_info),
            patch.object(discover, "looks_like_subscription", return_value=True),
            patch.object(discover.time, "sleep"),
            redirect_stderr(stderr),
        ):
            discover.main()

        self.assertNotIn("ghp_", stderr.getvalue())
        self.assertIn("owner/repo", stderr.getvalue())

    def test_screen_fetch_masks_blocked_url_in_returned_error(self):
        token = "ghp_abcdefghijklmnopqrstuvwxyz012345"
        url = f"https://feed.example/sub/{token}"
        with patch.object(
            screen.netsafe, "open_response",
            side_effect=netsafe.Blocked(f"{token} blocked target {url}"),
        ):
            result = screen.fetch(url)

        self.assertNotIn("ghp_", result["err"])

    def test_screen_markdown_masks_urls_reasons_and_notes(self):
        url_token = "AbCdEfGhIjKlMnOpQrStUvWxYz"
        reason_token = "ghp_abcdefghijklmnopqrstuvwxyz012345"
        note_token = "xox" + "p-" + "not-a-real-test-fixture"
        record = {
            "repo": "owner/repo",
            "verdict": "unverified",
            "url": f"https://feed.example/sub/{url_token}",
            "reasons": [f"blocked URL https://feed.example/api?ref={reason_token}"],
            "note": f"upstream returned {note_token}",
        }
        with tempfile.TemporaryDirectory(prefix="p24-redaction-report-") as td:
            output = Path(td) / "screening.md"
            with warnings.catch_warnings(record=True) as emitted:
                warnings.simplefilter("always", ResourceWarning)
                with patch.object(screen, "MD_PATH", str(output)):
                    screen.write_md([record], 1)
            self.assertFalse(any(issubclass(w.category, ResourceWarning) for w in emitted))
            report = output.read_text(encoding="utf-8")

        for secret in (url_token, reason_token, note_token):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, report)
        self.assertIn("owner/repo", report)

    def test_screen_markdown_keeps_each_record_reasons_separate(self):
        records = [
            {"repo": "owner/first", "verdict": "unverified",
             "reasons": ["first-only reason"]},
            {"repo": "owner/second", "verdict": "unverified",
             "reasons": ["second-only reason"]},
        ]
        with tempfile.TemporaryDirectory(prefix="p24-redaction-report-") as td:
            output = Path(td) / "screening.md"
            with patch.object(screen, "MD_PATH", str(output)):
                screen.write_md(records, 1)
            report = output.read_text(encoding="utf-8")

        first_section = report.split("### `owner/first`", 1)[1].split("\n### `", 1)[0]
        second_section = report.split("### `owner/second`", 1)[1].split("\n### `", 1)[0]
        self.assertIn("first-only reason", first_section)
        self.assertNotIn("second-only reason", first_section)
        self.assertIn("second-only reason", second_section)
        self.assertNotIn("first-only reason", second_section)

    def test_screen_main_masks_serialized_json_output(self):
        url_secret = "AbCdEfGhIjKlMnOpQrStUvWxYz"
        reason_secret = "ghp_abcdefghijklmnopqrstuvwxyz012345"
        candidate = {
            "repo": "owner/repo",
            "kind": "source",
            "url": f"https://feed.example/sub/{url_secret}",
        }
        result = dict(candidate, verdict="unverified",
                      reasons=[f"blocked token {reason_secret}"], note="reviewed")

        with tempfile.TemporaryDirectory(prefix="p24-redaction-json-") as td:
            output = Path(td) / "screening.json"
            markdown = Path(td) / "screening.md"
            with warnings.catch_warnings(record=True) as emitted:
                warnings.simplefilter("always", ResourceWarning)
                with (
                    patch.object(screen, "CANDIDATES", [candidate]),
                    patch.object(screen, "CAND_PATH", str(Path(td) / "absent.json")),
                    patch.object(screen, "OUT_PATH", str(output)),
                    patch.object(screen, "MD_PATH", str(markdown)),
                    patch.object(screen, "lib_keys", return_value=(set(), 1)),
                    patch.object(screen, "enabled_index", return_value=(set(), set())),
                    patch.object(screen, "screen_one", return_value=result),
                    patch.object(screen.sys, "argv", ["screen.py"]),
                    redirect_stdout(io.StringIO()),
                ):
                    screen.main()
            self.assertFalse(any(issubclass(w.category, ResourceWarning) for w in emitted))
            serialized = output.read_text(encoding="utf-8")

        self.assertNotIn(url_secret, serialized)
        self.assertNotIn(reason_secret, serialized)
        self.assertIn("owner/repo", serialized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
