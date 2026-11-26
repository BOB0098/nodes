#!/usr/bin/env python3
"""Offline regressions for candidate queue and screening artifact boundaries."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import discover  # noqa: E402
import screen  # noqa: E402
import sources_io  # noqa: E402


class CandidateArtifactSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _fail_replace_for(self, target):
        def fail_replace(src, dst):
            if Path(dst) == target:
                raise OSError("simulated atomic replacement failure")
            raise AssertionError(f"unexpected replacement target: {dst}")
        return fail_replace

    def test_discover_known_repos_fails_on_malformed_queue(self):
        queue = self.root / "candidates.json"
        original = '{"candidates": [broken'
        queue.write_text(original, encoding="utf-8")
        with patch.object(discover, "OUT", str(queue)):
            with self.assertRaises(ValueError):
                discover.known_repos()
        self.assertEqual(queue.read_text(encoding="utf-8"), original)

    def test_discover_refuses_to_overwrite_malformed_existing_queue(self):
        queue = self.root / "candidates.json"
        original = "not valid JSON"
        queue.write_text(original, encoding="utf-8")
        with patch.object(discover, "OUT", str(queue)), \
             patch.object(discover, "QUERIES", []), \
             patch.object(discover, "known_repos", return_value=set()):
            with self.assertRaises(ValueError):
                discover.main()
        self.assertEqual(queue.read_text(encoding="utf-8"), original)

    def test_discover_queue_write_preserves_old_queue_if_replace_fails(self):
        queue = self.root / "candidates.json"
        original = json.dumps({"candidates": [{
            "repo": "old/repo", "url": "https://raw.githubusercontent.com/old/repo/main/sub"
        }]}, ensure_ascii=False)
        queue.write_text(original, encoding="utf-8")
        with patch.object(discover, "OUT", str(queue)), \
             patch.object(discover, "QUERIES", []), \
             patch.object(discover, "known_repos", return_value=set()), \
             patch.object(discover.netsafe.os, "replace",
                          side_effect=self._fail_replace_for(queue)):
            with self.assertRaisesRegex(OSError, "simulated atomic replacement failure"):
                discover.main()
        self.assertEqual(queue.read_text(encoding="utf-8"), original)
        self.assertEqual(list(self.root.glob(".tmp-*.part")), [])

    def test_screen_refuses_malformed_queue_without_replacing_reports(self):
        queue = self.root / "candidates.json"
        report = self.root / "screening.json"
        markdown = self.root / "screening.md"
        original_queue = "[]"
        original_report = '{"old": true}'
        original_markdown = "old report\n"
        queue.write_text(original_queue, encoding="utf-8")
        report.write_text(original_report, encoding="utf-8")
        markdown.write_text(original_markdown, encoding="utf-8")
        with patch.object(screen, "CAND_PATH", str(queue)), \
             patch.object(screen, "OUT_PATH", str(report)), \
             patch.object(screen, "MD_PATH", str(markdown)), \
             patch.object(screen, "CANDIDATES", []), \
             patch.object(screen, "lib_keys", return_value=(set(), 0)), \
             patch.object(screen, "enabled_index", return_value=(set(), set())), \
             patch.object(screen.sys, "argv", ["screen.py"]):
            with self.assertRaises(ValueError):
                screen.main()
        self.assertEqual(queue.read_text(encoding="utf-8"), original_queue)
        self.assertEqual(report.read_text(encoding="utf-8"), original_report)
        self.assertEqual(markdown.read_text(encoding="utf-8"), original_markdown)

    def test_screen_json_report_is_preserved_when_atomic_replace_fails(self):
        queue = self.root / "missing-candidates.json"
        report = self.root / "screening.json"
        markdown = self.root / "screening.md"
        original_report = '{"old": true}'
        report.write_text(original_report, encoding="utf-8")
        markdown.write_text("old report\n", encoding="utf-8")
        with patch.object(screen, "CAND_PATH", str(queue)), \
             patch.object(screen, "OUT_PATH", str(report)), \
             patch.object(screen, "MD_PATH", str(markdown)), \
             patch.object(screen, "CANDIDATES", []), \
             patch.object(screen, "lib_keys", return_value=(set(), 0)), \
             patch.object(screen, "enabled_index", return_value=(set(), set())), \
             patch.object(screen.sys, "argv", ["screen.py"]), \
             patch.object(screen.netsafe.os, "replace",
                          side_effect=self._fail_replace_for(report)):
            with self.assertRaisesRegex(OSError, "simulated atomic replacement failure"):
                screen.main()
        self.assertEqual(report.read_text(encoding="utf-8"), original_report)
        self.assertEqual(markdown.read_text(encoding="utf-8"), "old report\n")
        self.assertEqual(list(self.root.glob(".tmp-*.part")), [])

    def test_screen_markdown_report_is_preserved_when_atomic_replace_fails(self):
        markdown = self.root / "screening.md"
        original = "old report\n"
        markdown.write_text(original, encoding="utf-8")
        with patch.object(screen, "MD_PATH", str(markdown)), \
             patch.object(screen.netsafe.os, "replace",
                          side_effect=self._fail_replace_for(markdown)):
            with self.assertRaisesRegex(OSError, "simulated atomic replacement failure"):
                screen.write_md([], 0)
        self.assertEqual(markdown.read_text(encoding="utf-8"), original)
        self.assertEqual(list(self.root.glob(".tmp-*.part")), [])

    def test_screen_does_not_silently_hide_source_registration_errors(self):
        with patch.object(screen.sources_io, "load",
                          side_effect=sources_io.SourceError("invalid source registry")):
            with self.assertRaisesRegex(sources_io.SourceError, "invalid source registry"):
                screen.enabled_index()


if __name__ == "__main__":
    unittest.main(verbosity=2)
