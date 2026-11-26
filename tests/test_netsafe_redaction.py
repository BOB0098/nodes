#!/usr/bin/env python3
"""P24 C6 回归：URL 与错误日志不能泄漏路径或普通参数中的凭据。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import netsafe  # noqa: E402


class NetsafeRedactionTest(unittest.TestCase):
    def test_masks_long_alphabetic_path_token(self):
        token = "AbCdEfGhIjKlMnOpQrStUvWxYz"

        masked = netsafe.mask_url(f"https://feed.example/sub/{token}")

        self.assertNotIn(token, masked)
        self.assertIn("/sub/", masked)

    def test_masks_long_path_token_with_only_one_digit(self):
        token = "AbCdEfGhIjKlMnOpQrStUvWx1"

        masked = netsafe.mask_url(f"https://feed.example/sub/{token}")

        self.assertNotIn(token, masked)

    def test_masks_known_token_in_ordinary_query_parameter(self):
        token = "ghp_abcdefghijklmnopqrstuvwxyz012345"

        masked = netsafe.mask_url(
            f"https://feed.example/api?ref={token}&page=2")

        self.assertNotIn(token, masked)
        self.assertIn("page=2", masked)

    def test_masks_high_entropy_value_in_ordinary_query_parameter(self):
        value = "aB3dE5gH7jK9mN2pQ4rT6vW8xY0zC1"

        masked = netsafe.mask_url(f"https://feed.example/api?ref={value}")

        self.assertNotIn(value, masked)

    def test_masks_token_after_multiple_url_decoding_layers(self):
        token = "ghp_abcdefghijklmnopqrstuvwxyz012345"
        nested = "ghp_%2561bcdefghijklmnopqrstuvwxyz012345"

        masked = netsafe.mask_url(f"https://feed.example/api?ref={nested}")

        self.assertNotIn(nested, masked)
        self.assertNotIn(token, masked)

    def test_masks_jwt_shaped_value_in_an_ordinary_parameter(self):
        jwt = ("eyJhbGciOiJIUzI1NiJ9."
               "eyJzdWIiOiIxMjM0NTY3ODkwIn0.signatureSECRET123456")

        masked = netsafe.mask_url(f"https://feed.example/api?ref={jwt}")

        self.assertNotIn(jwt, masked)

    def test_masks_uri_userinfo_even_when_it_contains_a_slash(self):
        uri = "vless://user:pa/ss@example.com:443?security=tls"

        masked = netsafe.mask_text(uri)

        self.assertNotIn("user:pa/ss", masked)
        self.assertIn("vless://***@example.com:443", masked)

    def test_masks_path_token_in_error_log_url(self):
        token = "AbCdEfGhIjKlMnOpQrStUvWxYz"
        error = f"HTTP Error 403: https://feed.example/sub/{token}"

        masked = netsafe.mask_text(error)

        self.assertNotIn(token, masked)
        self.assertIn("feed.example", masked)

    def test_preserves_readable_long_path(self):
        path = "internationalization-guide"

        masked = netsafe.mask_url(f"https://feed.example/docs/{path}")

        self.assertIn(path, masked)


if __name__ == "__main__":
    unittest.main(verbosity=2)
