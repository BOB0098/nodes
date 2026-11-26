#!/usr/bin/env python3
"""Offline regressions for opaque URI quarantine redaction."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import collector  # noqa: E402
import nodes_parse  # noqa: E402


class OpaqueUriQuarantineTest(unittest.TestCase):
    def build_public_quarantine(self, uri):
        rejects = []
        output = io.StringIO()
        with redirect_stdout(output):
            nodes, raw_count = nodes_parse.parse_any(uri, rejects)
            with tempfile.TemporaryDirectory(prefix="opaque-quarantine-") as td:
                with patch.object(collector, "OUT_DATA", td):
                    doc = collector.write_quarantine(
                        {"offline-fixture": rejects}, "2026-11-26"
                    )
                written = (Path(td) / "quarantine.json").read_text(encoding="utf-8")
        return nodes, raw_count, rejects, doc, written, output.getvalue()

    def contains_payload(self, value, payload):
        if isinstance(value, str):
            return payload in value
        if isinstance(value, dict):
            return any(self.contains_payload(k, payload) or
                       self.contains_payload(v, payload)
                       for k, v in value.items())
        if isinstance(value, (list, tuple)):
            return any(self.contains_payload(item, payload) for item in value)
        return False

    def assert_opaque_payload_not_public(self, payload, doc, written, diagnostics):
        self.assertFalse(
            self.contains_payload(doc, payload),
            "opaque URI payload reached a public quarantine field",
        )
        self.assertFalse(
            self.contains_payload(json.loads(written), payload),
            "opaque URI payload reached the serialized quarantine artifact",
        )
        self.assertFalse(
            payload in diagnostics,
            "opaque URI payload reached diagnostic output",
        )

    def test_unsupported_ssr_opaque_payload_is_not_quarantined_or_logged(self):
        payload = "offline-ssr-opaque-fixture-91af37c2"

        nodes, raw_count, rejects, doc, written, diagnostics = self.build_public_quarantine(
            "ssr://" + payload
        )

        self.assertEqual(nodes, [])
        self.assertEqual(raw_count, 1)
        self.assertEqual(len(rejects), 1)
        self.assert_opaque_payload_not_public(payload, doc, written, diagnostics)

    def test_malformed_supported_opaque_uri_is_not_quarantined_or_logged(self):
        payload = "offline-vless-opaque-fixture-72be4d19"

        nodes, raw_count, rejects, doc, written, diagnostics = self.build_public_quarantine(
            "vless://" + payload
        )

        self.assertEqual(nodes, [])
        self.assertEqual(raw_count, 1)
        self.assertEqual(len(rejects), 1)
        self.assert_opaque_payload_not_public(payload, doc, written, diagnostics)

    def test_supported_uri_still_parses_and_quarantines_its_real_endpoint(self):
        rejected = []
        bad_uuid_uri = "vless://not-a-uuid@node.example.com:443?type=ws#offline-fixture"
        nodes, raw_count = nodes_parse.parse_any(bad_uuid_uri, rejected)

        self.assertEqual(nodes, [])
        self.assertEqual(raw_count, 1)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["type"], "vless")
        self.assertEqual(rejected[0]["server"], "node.example.com")
        self.assertEqual(rejected[0]["port"], 443)
        self.assertEqual(rejected[0]["reason"], nodes_parse.REASON["bad_uuid"])

        valid_nodes, valid_count = nodes_parse.parse_any(
            "vless://123e4567-e89b-12d3-a456-426614174000@node.example.com:443?type=ws"
        )
        self.assertEqual(valid_count, 1)
        self.assertEqual(len(valid_nodes), 1)
        self.assertEqual(valid_nodes[0]["server"], "node.example.com")
        self.assertEqual(valid_nodes[0]["port"], 443)


if __name__ == "__main__":
    unittest.main(verbosity=2)
