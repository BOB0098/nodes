#!/usr/bin/env python3
"""P24 回归：读取页面内嵌 DATA 后必须关闭 index.html 文件句柄。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build_manifest  # noqa: E402


class EmbeddedUpdatedFileLifecycleTest(unittest.TestCase):
    def test_closes_index_html_after_read(self):
        with tempfile.TemporaryDirectory(prefix="manifest-file-lifecycle-") as td:
            base = Path(td)
            (base / "index.html").write_text(
                'const DATA = {"updated": "11-25 14:02"};\n', encoding="utf-8")
            opened = []
            real_open = open

            def tracking_open(*args, **kwargs):
                handle = real_open(*args, **kwargs)
                opened.append(handle)
                return handle

            with (
                patch.object(build_manifest, "BASE", str(base)),
                patch("builtins.open", side_effect=tracking_open),
            ):
                self.assertEqual(build_manifest.embedded_updated(), "11-25 14:02")

            self.assertEqual(len(opened), 1)
            self.assertTrue(opened[0].closed, "embedded_updated must close index.html")


if __name__ == "__main__":
    unittest.main(verbosity=2)
