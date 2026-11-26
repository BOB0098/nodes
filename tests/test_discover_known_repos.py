#!/usr/bin/env python3
"""Regression tests for discover.known_repos source-list error handling."""
import gc
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import discover  # noqa: E402
import sources_io  # noqa: E402


class DiscoverKnownReposTest(unittest.TestCase):
    def _load_from(self, source_path):
        real_load = sources_io.load
        return patch.object(
            discover.sources_io,
            "load",
            lambda *args, **kwargs: real_load(str(source_path)),
        )

    def test_missing_sources_file_keeps_first_run_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "candidates.json"
            queue.write_text('{"candidates": [{"repo": "queued/repo"}]}', encoding="utf-8")
            missing = root / "sources.yaml"

            with self._load_from(missing), patch.object(discover, "OUT", str(queue)):
                self.assertEqual(discover.known_repos(), {"queued/repo"})

    def test_malformed_sources_file_fails_instead_of_becoming_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "sources.yaml"
            source_path.write_text("sources: [\n  - broken: [", encoding="utf-8")

            with self._load_from(source_path):
                with self.assertRaises(yaml.YAMLError):
                    discover.known_repos()

    def test_unreadable_sources_path_fails_instead_of_becoming_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "sources.yaml"
            source_path.mkdir()

            with self._load_from(source_path):
                with self.assertRaises(IsADirectoryError):
                    discover.known_repos()

    def test_invalid_source_schema_fails_with_source_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "sources.yaml"
            source_path.write_text("sources: []\n", encoding="utf-8")

            with self._load_from(source_path):
                with self.assertRaisesRegex(sources_io.SourceError, "sources.yaml 缺少 sources / groups"):
                    discover.known_repos()

    def test_candidate_queue_file_handle_is_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "sources.yaml"
            source_path.write_text("groups: []\nsources: []\n", encoding="utf-8")
            queue = root / "candidates.json"
            queue.write_text(
                '{"candidates": [{"repo": "queued/repo"}]}', encoding="utf-8")

            with self._load_from(source_path), patch.object(discover, "OUT", str(queue)):
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always", ResourceWarning)
                    self.assertEqual(discover.known_repos(), {"queued/repo"})
                    gc.collect()

            self.assertFalse(
                any(issubclass(w.category, ResourceWarning) for w in caught),
                "candidate queue file should be closed after reading",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
