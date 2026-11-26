#!/usr/bin/env python3
"""Manifest writes must preserve the last valid file if serialization fails."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build_manifest  # noqa: E402


class ManifestAtomicWriteTest(unittest.TestCase):
    def test_serialization_interruption_preserves_previous_manifest(self):
        with tempfile.TemporaryDirectory(prefix="manifest-atomic-write-") as td:
            target = Path(td) / "build_manifest.json"
            previous = b'{"build_id":"previous","valid":true}\n'
            target.write_bytes(previous)

            def fail_after_partial_json(self, obj, _one_shot=False):
                yield '{"build_id":"next",'
                raise OSError("simulated serialization interruption")

            with (
                patch.object(build_manifest, "MANIFEST", str(target)),
                patch.object(build_manifest, "build", return_value={"build_id": "next"}),
                patch.object(build_manifest, "verify", return_value=(True, [])),
                patch.object(build_manifest.sys, "argv", ["build_manifest.py", "--write"]),
                patch.object(
                    build_manifest.json.JSONEncoder,
                    "iterencode",
                    fail_after_partial_json,
                ),
            ):
                with self.assertRaisesRegex(OSError, "simulated serialization interruption"):
                    build_manifest.main()

            self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {
                "build_id": "previous",
                "valid": True,
            })
            self.assertEqual(list(target.parent.glob(".tmp-*.part")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
