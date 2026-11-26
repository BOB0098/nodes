#!/usr/bin/env python3
"""P24 回归：低计数保护保留旧数据且必须返回失败状态。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import collector  # noqa: E402


class CollectorLowCountTest(unittest.TestCase):
    def test_incomplete_collection_keeps_old_snapshot_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory(prefix="p24-low-count-") as td:
            data_dir = Path(td)
            nodes_path = data_dir / "nodes.json"
            previous = {"nodes": [{"addr": f"node-{i}"} for i in range(100)]}
            original_bytes = json.dumps(previous).encode("utf-8")
            nodes_path.write_bytes(original_bytes)

            with (
                patch.object(collector, "OUT_DATA", str(data_dir)),
                patch.object(collector, "collect", return_value=([], [], [], 0, {}, {})),
            ):
                with self.assertRaises(SystemExit) as raised:
                    collector.main()

            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(nodes_path.read_bytes(), original_bytes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
