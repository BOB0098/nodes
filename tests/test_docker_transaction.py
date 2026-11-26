#!/usr/bin/env python3
"""P24 回归：容器事务失败恢复仅回滚产物，不回滚运行日志。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker"))

import transaction  # noqa: E402


class DockerTransactionTest(unittest.TestCase):
    def test_restore_reverts_outputs_removes_new_files_and_keeps_logs(self):
        with tempfile.TemporaryDirectory(prefix="p24-docker-tx-") as td:
            base = Path(td)
            root = base / "app"
            backup = base / "backup"
            (root / "data" / "logs").mkdir(parents=True)
            (root / "data" / "nodes.json").write_text("old nodes", encoding="utf-8")
            (root / "data" / "node_history.json").write_text("old history", encoding="utf-8")
            (root / "data" / "logs" / "cron.log").write_text("old log", encoding="utf-8")
            (root / "index.html").write_text("old page", encoding="utf-8")
            (root / "docs").mkdir()
            (root / "docs" / "P4-source-quality.md").write_text("old report", encoding="utf-8")
            (root / "docs" / "P19-governance.md").write_text("old governance", encoding="utf-8")
            (root / "export").mkdir()
            (root / "export" / "private.txt").write_text("old private", encoding="utf-8")

            transaction.snapshot(root, backup)

            (root / "data" / "nodes.json").write_text("new nodes", encoding="utf-8")
            (root / "data" / "node_history.json").unlink()
            (root / "data" / "new-output.json").write_text("new", encoding="utf-8")
            (root / "data" / "logs" / "cron.log").write_text("old log\nfailed run", encoding="utf-8")
            (root / "index.html").write_text("new page", encoding="utf-8")
            (root / "docs" / "P4-source-quality.md").write_text("new report", encoding="utf-8")
            (root / "docs" / "P19-governance.md").unlink()
            (root / "export" / "private.txt").write_text("new private", encoding="utf-8")
            (root / "export" / "new-secret.txt").write_text("new secret", encoding="utf-8")

            with patch.object(transaction, "_remove", wraps=transaction._remove) as remove:
                transaction.restore(root, backup)
            self.assertNotIn(call(root / "index.html"), remove.call_args_list)

            self.assertEqual((root / "data" / "nodes.json").read_text(), "old nodes")
            self.assertEqual((root / "data" / "node_history.json").read_text(), "old history")
            self.assertFalse((root / "data" / "new-output.json").exists())
            self.assertEqual((root / "data" / "logs" / "cron.log").read_text(), "old log\nfailed run")
            self.assertEqual((root / "index.html").read_text(), "old page")
            self.assertEqual((root / "docs" / "P4-source-quality.md").read_text(), "old report")
            self.assertEqual((root / "docs" / "P19-governance.md").read_text(), "old governance")
            self.assertEqual((root / "export" / "private.txt").read_text(), "old private")
            self.assertFalse((root / "export" / "new-secret.txt").exists())

    def test_restore_removes_outputs_that_did_not_exist_in_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="p24-docker-tx-empty-") as td:
            base = Path(td)
            root = base / "app"
            backup = base / "backup"
            (root / "data" / "logs").mkdir(parents=True)
            (root / "data" / "logs" / "run.log").write_text("keep", encoding="utf-8")
            transaction.snapshot(root, backup)

            (root / "index.html").write_text("new page", encoding="utf-8")
            (root / "docs").mkdir()
            (root / "docs" / "P4-source-quality.md").write_text("new", encoding="utf-8")
            (root / "export").mkdir()
            (root / "export" / "new.txt").write_text("new", encoding="utf-8")
            (root / "data" / "new.json").write_text("new", encoding="utf-8")

            transaction.restore(root, backup)

            self.assertFalse((root / "index.html").exists())
            self.assertFalse((root / "docs" / "P4-source-quality.md").exists())
            self.assertFalse((root / "export").exists())
            self.assertFalse((root / "data" / "new.json").exists())
            self.assertEqual((root / "data" / "logs" / "run.log").read_text(), "keep")

    def test_snapshot_rejects_backup_inside_app_root(self):
        with tempfile.TemporaryDirectory(prefix="p24-docker-tx-path-") as td:
            root = Path(td) / "app"
            root.mkdir()
            with self.assertRaisesRegex(ValueError, "outside APP_ROOT"):
                transaction.snapshot(root, root / "backup")


if __name__ == "__main__":
    unittest.main(verbosity=2)
