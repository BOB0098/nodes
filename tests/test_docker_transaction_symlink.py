#!/usr/bin/env python3
"""P24 回归：容器事务拒绝通过输出路径符号链接触及 APP_ROOT 外部。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker"))

import transaction  # noqa: E402


class DockerTransactionSymlinkTest(unittest.TestCase):
    def _fixture(self, rel):
        td = tempfile.TemporaryDirectory(prefix="p24-docker-symlink-")
        self.addCleanup(td.cleanup)
        base = Path(td.name)
        root = base / "app"
        outside = base / "outside"
        root.mkdir()
        outside.mkdir()
        sentinel = outside / "keep.txt"
        sentinel.write_text("outside data must remain", encoding="utf-8")

        linked = root / rel
        linked.parent.mkdir(parents=True, exist_ok=True)
        if rel == "index.html":
            linked.symlink_to(sentinel)
        else:
            linked.symlink_to(outside, target_is_directory=True)

        backup = base / "backup"
        backup.mkdir()
        state = {key: False for key in transaction.OUTPUTS}
        (backup / "state.json").write_text(json.dumps(state), encoding="utf-8")
        return root, backup, sentinel

    def test_snapshot_rejects_symlinked_output_paths_before_writing_backup(self):
        for rel in ("data", "docs", "export", "index.html"):
            with self.subTest(rel=rel):
                root, backup, sentinel = self._fixture(rel)
                state_before = (backup / "state.json").read_bytes()
                (backup / "marker").write_text("existing backup", encoding="utf-8")

                with self.assertRaisesRegex(ValueError, "symlink|symbolic"):
                    transaction.snapshot(root, backup)

                self.assertEqual(sentinel.read_text(encoding="utf-8"),
                                 "outside data must remain")
                self.assertEqual((backup / "marker").read_text(encoding="utf-8"),
                                 "existing backup")
                self.assertEqual((backup / "state.json").read_bytes(), state_before)

    def test_restore_rejects_symlinked_output_paths_before_external_mutation(self):
        for rel in ("data", "docs", "export", "index.html"):
            with self.subTest(rel=rel):
                root, backup, sentinel = self._fixture(rel)

                with self.assertRaisesRegex(ValueError, "symlink|symbolic"):
                    transaction.restore(root, backup)

                self.assertEqual(sentinel.read_text(encoding="utf-8"),
                                 "outside data must remain")
                self.assertTrue((root / rel).is_symlink())


if __name__ == "__main__":
    unittest.main(verbosity=2)
