#!/usr/bin/env python3
"""检查 template.html 是 index.html 的真实来源，并锁定 DOM 清空语义。"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import collector  # noqa: E402


class TemplateBuildOutputTest(unittest.TestCase):
    def test_generated_page_matches_source_template_and_embedded_data(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        match = re.search(r"const DATA = (\{.*?\});\s*\n", html, re.S)
        self.assertIsNotNone(match, "index.html 缺少内嵌 DATA")
        page = json.loads(match.group(1))
        self.assertEqual(collector.render_page(page), html)

    def test_clearing_uses_replace_children_without_html_parser(self):
        template = (ROOT / "template.html").read_text(encoding="utf-8")
        match = re.search(r"<script>(.*?)</script>", template, re.S)
        self.assertIsNotNone(match, "template.html 缺少主脚本")
        script = match.group(1)
        self.assertEqual(script.count(".replaceChildren()"), 4)
        self.assertIsNone(
            re.search(r"innerHTML\s*=\s*(['\"])(?:\1)", script),
            "仍有通过 innerHTML 空字符串清除节点的调用",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
