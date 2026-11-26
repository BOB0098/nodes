#!/usr/bin/env python3
"""检查 template.html 是 index.html 的真实来源，并锁定页面构建契约。"""
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import collector  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def embedded_json(html, name):
    match = re.search(rf"^const {re.escape(name)} = (.*);$", html, re.M)
    if not match:
        return None
    return json.loads(match.group(1))


class TemplateBuildOutputTest(unittest.TestCase):
    def test_generated_page_matches_template_and_tracked_embedded_data(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        page_match = re.search(r"const DATA = (\{.*?\});\s*\n", html, re.S)
        self.assertIsNotNone(page_match, "index.html 缺少内嵌 DATA")
        page = json.loads(page_match.group(1))
        governance = json.loads((ROOT / "data/source_governance.json").read_text(encoding="utf-8"))
        picks = json.loads((ROOT / "data/picks.json").read_text(encoding="utf-8"))
        self.assertEqual(embedded_json(html, "GOVERNANCE"), governance,
                         "治理 JSON 必须构建时内嵌到单文件页面")
        self.assertEqual(embedded_json(html, "PICKS"), picks,
                         "榜单 JSON 必须构建时内嵌到单文件页面")
        self.assertEqual(collector.render_page(page), html)

    def test_render_page_escapes_untrusted_auxiliary_json_for_script_context(self):
        marker = '</script><script>globalThis.__p24_injected = true</script>'
        governance = {"window": 30, "note": marker}
        picks = {"lists": {"stable": [{"name": marker}]}}
        try:
            html = collector.render_page(
                {"schema": 2, "groups": []}, governance=governance, picks=picks)
        except TypeError as exc:
            self.fail(f"render_page 尚未支持安全内嵌治理与榜单数据：{exc}")

        self.assertEqual(embedded_json(html, "GOVERNANCE"), governance)
        self.assertEqual(embedded_json(html, "PICKS"), picks)
        self.assertEqual(html.count("<script"), 1, "输入数据不能注入额外脚本元素")
        self.assertNotIn(marker, html, "危险 HTML 结束标签必须在脚本数据上下文转义")

    def test_missing_or_corrupt_sidecars_build_null_fallbacks(self):
        with tempfile.TemporaryDirectory(prefix="nodes-sidecar-fallback-") as td:
            Path(td, "source_governance.json").write_text("{broken", encoding="utf-8")
            with patch.object(collector, "OUT_DATA", td):
                html = collector.render_page({"schema": 2, "groups": []})

        self.assertIsNone(embedded_json(html, "GOVERNANCE"))
        self.assertIsNone(embedded_json(html, "PICKS"))

    def test_untrusted_count_renders_as_text_in_file_page(self):
        chromium = (shutil.which("chromium") or shutil.which("chromium-browser")
                    or shutil.which("google-chrome"))
        if sync_playwright is None or chromium is None:
            self.skipTest("Playwright and a system Chromium are required for this browser check")

        tracked = (ROOT / "index.html").read_text(encoding="utf-8")
        match = re.search(r"const DATA = (\{.*?\});\s*\n", tracked, re.S)
        self.assertIsNotNone(match)
        page_data = json.loads(match.group(1))
        item = next(item for group in page_data["groups"] for item in group["items"])
        marker = '</b><svg onload="window.__p24_count_injected=true"></svg><b>'
        item["count"] = marker
        output = collector.render_page(page_data)

        with tempfile.TemporaryDirectory(prefix="nodes-offline-count-") as td:
            path = Path(td, "index.html")
            path.write_text(output, encoding="utf-8")
            requests = []
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=chromium, headless=True, args=["--no-sandbox"])
                try:
                    page = browser.new_page()
                    page.on("request", lambda request: requests.append(request.url))
                    page.goto(path.as_uri())
                    page.wait_for_selector(".row .r2")
                    self.assertFalse(page.evaluate("Boolean(window.__p24_count_injected)"))
                    self.assertEqual(page.locator(".row .r2 svg").count(), 0)
                    self.assertIn(marker, page.locator(".row .r2").first.inner_text())
                    self.assertFalse(any(url.startswith(("http:", "https:")) for url in requests))
                finally:
                    browser.close()

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
