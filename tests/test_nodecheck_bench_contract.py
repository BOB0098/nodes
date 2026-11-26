#!/usr/bin/env python3
"""P24 回归：探测必须只接受 204，bench 必须统计阶段化 timeout 错误。"""
import builtins
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bench  # noqa: E402
import nodecheck  # noqa: E402


class NodecheckBenchContractTest(unittest.TestCase):
    def test_probe_rejects_http_200_and_accepts_http_204(self):
        responses = [
            SimpleNamespace(returncode=0, stdout="200"),
            SimpleNamespace(returncode=0, stdout="204"),
        ]
        with patch.object(nodecheck.subprocess, "run", side_effect=responses):
            http_200 = nodecheck.probe(23456)
            http_204 = nodecheck.probe(23456)

        self.assertFalse(http_200[0])
        self.assertEqual(http_200[2], "http:200")
        self.assertTrue(http_204[0])
        self.assertIsNone(http_204[2])

    def test_probe_timeout_expired_is_reported_as_countable_timeout(self):
        with patch.object(
                nodecheck.subprocess, "run",
                side_effect=nodecheck.subprocess.TimeoutExpired("curl", 8)):
            result = nodecheck.probe(23456, timeout=8)

        self.assertEqual(result, (False, 8000, "timeout:超时"))

    def test_net_one_counts_stage_prefixed_timeouts(self):
        nodes = [
            {"fp": "timeout", "src": "fixture"},
            {"fp": "tcp", "src": "fixture"},
            {"fp": "ok", "src": "fixture"},
        ]
        results = {
            "timeout": {"ok": False, "err": "timeout:超时"},
            "tcp": {"ok": False, "err": "tcp:连接失败"},
            "ok": {"ok": True, "ms": 100},
        }
        full_path = "/p24-fixture/nodes_full.json"
        original_open = builtins.open

        def fixture_open(path, *args, **kwargs):
            if str(path) == full_path:
                return io.StringIO(json.dumps(nodes))
            return original_open(path, *args, **kwargs)

        with (
            patch.object(bench, "FULL", full_path),
            patch("builtins.open", side_effect=fixture_open),
            patch.object(nodecheck, "_port_free", return_value=True),
            patch.object(nodecheck, "run_batch", return_value=results),
        ):
            measured = bench.net_one(3)

        self.assertEqual(measured["timeout_pct"], 33.3)
        self.assertEqual(measured["fail_pct"], 66.7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
