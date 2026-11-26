#!/usr/bin/env python3
"""P24 回归：隐私审计的日志输入失败闭合与常见秘密识别。"""
import builtins
import base64
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import audit_privacy  # noqa: E402


class AuditPrivacyRegressionTest(unittest.TestCase):
    def run_isolated(self, *args):
        output = io.StringIO()
        argv = ["audit_privacy.py", *args]
        with (
            patch.object(audit_privacy, "public_files", return_value=[]),
            patch.object(audit_privacy.sys, "argv", argv),
            redirect_stdout(output),
        ):
            code = audit_privacy.main()
        return code, output.getvalue()

    def make_public_tree(self, base, *, index_kind="file"):
        data_dir = base / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "safe.json").write_text('{"ok": true}', encoding="utf-8")
        if index_kind == "file":
            (base / "index.html").write_text("safe page", encoding="utf-8")
        elif index_kind == "directory":
            (base / "index.html").mkdir()

    def run_public_scan(self, base, opener=None):
        output = io.StringIO()
        argv = ["audit_privacy.py"]
        with (
            patch.object(audit_privacy, "BASE", str(base)),
            patch.object(audit_privacy.sys, "argv", argv),
            redirect_stdout(output),
        ):
            if opener is None:
                code = audit_privacy.main()
            else:
                with patch.object(builtins, "open", side_effect=opener):
                    code = audit_privacy.main()
        return code, output.getvalue()

    def assert_public_scan_fails_closed(self, code, output, secret=None):
        self.assertNotEqual(code, 0)
        self.assertNotIn("允许发布", output)
        self.assertNotIn("未发现凭据/令牌泄漏", output)
        if secret is not None:
            self.assertNotIn(secret, output)

    def test_missing_public_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="p24-audit-privacy-") as td:
            base = Path(td)
            self.make_public_tree(base, index_kind=None)

            code, output = self.run_public_scan(base)

        self.assert_public_scan_fails_closed(code, output)

    def test_permission_denied_public_artifact_is_rejected_without_error_text(self):
        secret = "ghp_" + "Z" * 40
        with tempfile.TemporaryDirectory(prefix="p24-audit-privacy-") as td:
            base = Path(td)
            self.make_public_tree(base)
            real_open = builtins.open

            def deny_index(path, *args, **kwargs):
                if str(path) == str(base / "index.html"):
                    raise PermissionError(f"denied while reading {secret}")
                return real_open(path, *args, **kwargs)

            code, output = self.run_public_scan(base, deny_index)

        self.assert_public_scan_fails_closed(code, output, secret)

    def test_directory_in_place_of_public_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="p24-audit-privacy-") as td:
            base = Path(td)
            self.make_public_tree(base, index_kind="directory")

            code, output = self.run_public_scan(base)

        self.assert_public_scan_fails_closed(code, output)

    def test_mid_read_error_is_rejected_without_error_text(self):
        secret = "ghp_" + "Y" * 40
        with tempfile.TemporaryDirectory(prefix="p24-audit-privacy-") as td:
            base = Path(td)
            self.make_public_tree(base)
            real_open = builtins.open

            class InterruptedRead:
                def __init__(self, stream):
                    self.stream = stream

                def __enter__(self):
                    return self

                def __exit__(self, *_exc):
                    self.stream.close()

                def read(self, *args, **kwargs):
                    self.stream.read(16)
                    raise OSError(f"read interrupted after partial data: {secret}")

            def interrupt_index(path, *args, **kwargs):
                stream = real_open(path, *args, **kwargs)
                if str(path) == str(base / "index.html"):
                    return InterruptedRead(stream)
                return stream

            code, output = self.run_public_scan(base, interrupt_index)

        self.assert_public_scan_fails_closed(code, output, secret)

    def test_log_option_without_path_is_rejected(self):
        code, output = self.run_isolated("--log")

        self.assertNotEqual(code, 0)
        self.assertNotIn("允许发布", output)

    def test_log_option_followed_by_another_flag_is_rejected(self):
        code, output = self.run_isolated("--log", "--git")

        self.assertNotEqual(code, 0)
        self.assertNotIn("允许发布", output)

    def test_missing_log_file_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="p24-audit-privacy-") as td:
            absent = Path(td) / "absent.log"
            code, output = self.run_isolated("--log", str(absent))

        self.assertNotEqual(code, 0)
        self.assertNotIn("允许发布", output)

    def test_unreadable_log_file_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="p24-audit-privacy-") as td:
            log_path = Path(td) / "denied.log"
            log_path.write_text("offline fixture", encoding="utf-8")
            output = io.StringIO()
            argv = ["audit_privacy.py", "--log", str(log_path)]
            with (
                patch.object(audit_privacy, "public_files", return_value=[]),
                patch.object(audit_privacy.sys, "argv", argv),
                patch.object(builtins, "open", side_effect=PermissionError),
                redirect_stdout(output),
            ):
                code = audit_privacy.main()

        self.assertNotEqual(code, 0)
        self.assertNotIn("允许发布", output.getvalue())

    def test_common_credential_field_names_are_detected(self):
        examples = (
            ('"api_key": "fixture-api-key-value-123456"', "api_key"),
            ('"access_key": "fixture-access-key-value-123456"', "access_key"),
            ('"client_secret": "fixture-client-secret-value-123456"', "client_secret"),
            ("API_KEY=fixture-api-key-value-123456", "API_KEY"),
            ("access-key: fixture-access-key-value-123456", "access-key"),
        )

        for text, field in examples:
            with self.subTest(field=field):
                hits = audit_privacy.audit_text(text, "fixture.json")
                self.assertTrue(hits, f"未识别字段 {field}")

    def test_high_entropy_bearer_and_urlsafe_tokens_are_detected(self):
        bearer = "A7xP2mQ9vL4cN8rT1yK6bD3"
        urlsafe = "Az9_Ab7-CD4_ef6-Gh2_Ij8-Kl5_Mn3-Pq1_Rs0"

        bearer_hits = audit_privacy.audit_text(
            f"Authorization: Bearer {bearer}", "fixture.log"
        )
        urlsafe_hits = audit_privacy.audit_text(urlsafe, "fixture.log")

        self.assertTrue(bearer_hits, "未识别高熵 Bearer token")
        self.assertTrue(urlsafe_hits, "未识别 URL-safe 高熵 token")

    def test_urlsafe_base64_encoded_credential_is_detected(self):
        payload = ('{"api_key":"🔥' + "a" * 40 + '"}').encode()
        encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")

        self.assertTrue("-" in encoded or "_" in encoded)
        self.assertTrue(audit_privacy.audit_text(encoded, "fixture.log"))

    def test_log_hit_report_does_not_print_credential_fragments(self):
        synthetic_token = "ghp_" + "A" * 36
        with tempfile.TemporaryDirectory(prefix="p24-audit-privacy-") as td:
            log_path = Path(td) / "fixture.log"
            log_path.write_text(f"token={synthetic_token}", encoding="utf-8")
            code, output = self.run_isolated("--log", str(log_path))

        self.assertEqual(code, 1)
        self.assertNotIn(synthetic_token[:16], output)
        self.assertNotIn(synthetic_token, output)

    def test_placeholders_and_existing_normal_text_stay_clean(self):
        safe_examples = (
            '{"api_key": "not configured"}',
            "API_KEY=not_configured",
            '{"client_secret": "replace me"}',
            "https://example.test/?api_key=not_configured",
            '"reason": "凭据不是合法 UUID（vmess/vless 无法握手）"',
            '"name": "示例 ss 节点"',
            "token=ghp_123456789012345678901234567890123456",
            "opaque low-entropy marker: " + "A" * 48,
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        )

        for text in safe_examples:
            with self.subTest(text_kind=text[:24]):
                self.assertEqual(audit_privacy.audit_text(text, "fixture"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
