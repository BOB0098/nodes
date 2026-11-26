#!/usr/bin/env python3
"""P24 privacy regression: keep reviewable raw URLs without query injection."""
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import discover  # noqa: E402


class DiscoverCandidateUrlTest(unittest.TestCase):
    def test_normal_candidate_url_stays_reviewable_and_unchanged(self):
        actual = discover.build_candidate_url(
            "free-nodes/clashfree", "main", "clash20261122.yml")

        self.assertEqual(
            actual,
            "https://raw.githubusercontent.com/free-nodes/clashfree/main/clash20261122.yml",
        )

    def test_branch_slashes_remain_path_and_filename_delimiters_are_escaped(self):
        actual = discover.build_candidate_url(
            "owner/repo", "feature/review", "feeds/list?token=private#part.yml")
        parsed = urlsplit(actual)

        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.hostname, "raw.githubusercontent.com")
        self.assertIsNone(parsed.username)
        self.assertIsNone(parsed.password)
        self.assertEqual(parsed.query, "")
        self.assertEqual(parsed.fragment, "")
        self.assertEqual(
            parsed.path,
            "/owner/repo/feature/review/feeds/list%3Ftoken%3Dprivate%23part.yml",
        )

    def test_invalid_repository_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            discover.build_candidate_url(
                "owner/repo?token=private", "main", "feed.yml")


if __name__ == "__main__":
    unittest.main(verbosity=2)
