import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sources_io


@contextmanager
def stats_file(contents=None):
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "sources_stats.json")
        if contents is not None:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(contents)
        with patch.object(sources_io, "STATS_PATH", path):
            yield path


class LoadStatsRegressionTests(unittest.TestCase):
    def test_first_run_missing_file_returns_empty_compatible_state(self):
        with stats_file() as path:
            self.assertFalse(os.path.exists(path))
            self.assertEqual(sources_io.load_stats(), {"sources": []})

    def test_corrupt_json_fails_clearly_and_preserves_original_file(self):
        original = '{"sources": [broken'
        with stats_file(original) as path:
            with self.assertRaisesRegex(sources_io.SourceError, "JSON"):
                sources_io.load_stats()
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), original)

    def test_invalid_schema_fails_clearly_and_preserves_original_file(self):
        original = json.dumps({"sources": {"not": "a list"}}, ensure_ascii=False)
        with stats_file(original) as path:
            with self.assertRaisesRegex(sources_io.SourceError, "schema|sources"):
                sources_io.load_stats()
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), original)

    def test_missing_source_id_fails_instead_of_dropping_history_mapping(self):
        original = json.dumps({"sources": [{"fails_total": 8, "fails_streak": 3}]})
        with stats_file(original) as path:
            with self.assertRaisesRegex(sources_io.SourceError, "id"):
                sources_io.load_stats()
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), original)

    def test_missing_failure_counters_fails_instead_of_resetting_history(self):
        original = json.dumps({"sources": [{"id": "source-1"}]})
        with stats_file(original) as path:
            with self.assertRaisesRegex(sources_io.SourceError, "fails_total|fails_streak"):
                sources_io.load_stats()
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), original)

    def test_invalid_failure_counter_fails_without_rewriting_original_file(self):
        original = json.dumps({
            "sources": [{"id": "source-1", "fails_total": -1, "fails_streak": 0}]
        })
        with stats_file(original) as path:
            with self.assertRaisesRegex(sources_io.SourceError, "非负整数"):
                sources_io.load_stats()
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), original)

    def test_permission_error_fails_clearly_and_preserves_original_file(self):
        original = json.dumps({"sources": []})
        with stats_file(original) as path:
            old_mode = stat.S_IMODE(os.stat(path).st_mode)
            try:
                os.chmod(path, 0)
                try:
                    result = sources_io.load_stats()
                except sources_io.SourceError as error:
                    self.assertRegex(str(error), "permission|读取|权限")
                else:
                    self.skipTest("当前运行环境允许所有者读取 chmod 000 文件，无法可靠模拟权限错误")
                with open(path, "r", encoding="utf-8") as handle:
                    self.fail("chmod 000 unexpectedly allowed test process to read file")
            except PermissionError:
                pass
            finally:
                os.chmod(path, old_mode)


if __name__ == "__main__":
    unittest.main()
