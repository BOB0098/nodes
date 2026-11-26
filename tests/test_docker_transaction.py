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

    def test_snapshot_rejects_nested_output_mount_before_touching_backup(self):
        with tempfile.TemporaryDirectory(prefix="p24-docker-mount-") as td:
            base = Path(td)
            root = base / "app"
            backup = base / "backup"
            (root / "data" / "nested").mkdir(parents=True)
            (root / "data" / "nodes.json").write_text("old", encoding="utf-8")
            backup.mkdir()
            (backup / "marker").write_text("keep", encoding="utf-8")
            mountinfo = "\n".join([
                f"36 25 0:32 / {root}/data rw - ext4 /dev/data rw",
                f"37 25 0:33 / {root}/data/nested rw - ext4 /dev/nested rw",
            ])

            with patch.object(transaction, "_read_mountinfo", create=True,
                              return_value=mountinfo):
                with self.assertRaisesRegex(ValueError, "mount"):
                    transaction.snapshot(root, backup)

            self.assertEqual((backup / "marker").read_text(), "keep")
            self.assertFalse((backup / "state.json").exists())

    def test_snapshot_rejects_unrelated_extra_mount_before_touching_backup(self):
        with tempfile.TemporaryDirectory(prefix="p24-docker-extra-mount-") as td:
            base = Path(td)
            root = base / "app"
            backup = base / "backup"
            (root / "data").mkdir(parents=True)
            (root / "index.html").write_text("page", encoding="utf-8")
            (root / "cache").mkdir()
            backup.mkdir()
            (backup / "marker").write_text("keep", encoding="utf-8")
            mountinfo = f"38 25 0:34 / {root}/cache rw - ext4 /dev/cache rw"

            with patch.object(transaction, "_read_mountinfo", create=True,
                              return_value=mountinfo):
                with self.assertRaisesRegex(ValueError, "mount"):
                    transaction.snapshot(root, backup)

            self.assertEqual((backup / "marker").read_text(), "keep")
            self.assertFalse((backup / "state.json").exists())

    def test_snapshot_allows_existing_compose_mounts(self):
        with tempfile.TemporaryDirectory(prefix="p24-docker-compose-mounts-") as td:
            base = Path(td)
            root = base / "app"
            backup = base / "backup"
            (root / "data").mkdir(parents=True)
            (root / "index.html").write_text("page", encoding="utf-8")
            (root / ".cache").mkdir()
            mountinfo = "\n".join([
                f"36 25 0:32 / {root}/data rw - ext4 /dev/data rw",
                f"37 25 0:33 / {root}/index.html rw - ext4 /dev/page rw",
                f"38 25 0:34 / {root}/.cache rw - ext4 /dev/cache rw",
            ])

            with patch.object(transaction, "_read_mountinfo", create=True,
                              return_value=mountinfo):
                transaction.snapshot(root, backup)

            state = (backup / "state.json").read_text(encoding="utf-8")
            self.assertIn('"index.html": true', state)
            self.assertIn('"data": true', state)

    def test_restore_stages_data_before_replacing_existing_outputs(self):
        with tempfile.TemporaryDirectory(prefix="p24-docker-restore-stage-") as td:
            base = Path(td)
            root = base / "app"
            backup = base / "backup"
            (root / "data").mkdir(parents=True)
            (root / "data" / "nodes.json").write_text("old", encoding="utf-8")
            transaction.snapshot(root, backup)
            (root / "data" / "nodes.json").write_text("new", encoding="utf-8")
            (root / "data" / "extra.json").write_text("new extra", encoding="utf-8")

            def fail_after_partial(_source, destination):
                Path(destination).write_text("partial", encoding="utf-8")
                raise OSError("simulated staged copy failure")

            with patch.object(transaction.shutil, "copy2", side_effect=fail_after_partial):
                with self.assertRaisesRegex(RuntimeError, "simulated staged copy failure"):
                    transaction.restore(root, backup)

            self.assertEqual((root / "data" / "nodes.json").read_text(), "new")
            self.assertFalse((root / "data" / "extra.json").exists())
            self.assertEqual((backup / "data" / "nodes.json").read_text(), "old")

    def test_restore_does_not_partially_overwrite_unmounted_index_file(self):
        with tempfile.TemporaryDirectory(prefix="p24-docker-page-restore-") as td:
            base = Path(td)
            root = base / "app"
            backup = base / "backup"
            root.mkdir()
            (root / "index.html").write_text("old page", encoding="utf-8")
            transaction.snapshot(root, backup)
            (root / "index.html").write_text("new page", encoding="utf-8")

            def fail_after_partial(_source, destination):
                Path(destination).write_text("partial page", encoding="utf-8")
                raise OSError("simulated page copy failure")

            with patch.object(transaction.shutil, "copy2", side_effect=fail_after_partial):
                with self.assertRaisesRegex(RuntimeError, "simulated page copy failure"):
                    transaction.restore(root, backup)

            self.assertEqual((root / "index.html").read_text(), "new page")
            self.assertEqual((backup / "index.html").read_text(), "old page")

    def test_restore_keeps_previous_copy_when_replacement_and_rollback_fail(self):
        with tempfile.TemporaryDirectory(prefix="p24-docker-rollback-fail-") as td:
            base = Path(td)
            root = base / "app"
            backup = base / "backup"
            output = root / "data" / "nodes.json"
            output.parent.mkdir(parents=True)
            output.write_text("before run", encoding="utf-8")
            transaction.snapshot(root, backup)
            output.write_text("failed run", encoding="utf-8")

            real_replace = transaction.os.replace
            def fail_replacement_and_rollback(source, destination):
                source = Path(source)
                if Path(destination) == output and source.name == "new":
                    output.write_text("partial replacement", encoding="utf-8")
                    raise OSError("simulated staged replacement failure")
                if Path(destination) == output and source.name == "previous":
                    raise OSError("simulated rollback failure")
                return real_replace(source, destination)

            with patch.object(transaction.os, "replace",
                               side_effect=fail_replacement_and_rollback):
                with self.assertRaisesRegex(RuntimeError, "previous output remains at"):
                    transaction.restore(root, backup)

            recovery_copies = list(root.glob("data/.nodes-restore-*/previous"))
            self.assertEqual(len(recovery_copies), 1,
                             "回滚失败时必须保留并报告 previous 恢复副本")
            self.assertEqual(recovery_copies[0].read_text(encoding="utf-8"), "failed run")
            self.assertEqual((backup / "data" / "nodes.json").read_text(encoding="utf-8"),
                             "before run")

    def test_restore_continues_after_one_data_item_fails(self):
        with tempfile.TemporaryDirectory(prefix="p24-docker-best-effort-") as td:
            base = Path(td)
            root = base / "app"
            backup = base / "backup"
            (root / "data").mkdir(parents=True)
            (root / "data" / "nodes.json").write_text("old nodes", encoding="utf-8")
            (root / "data" / "status.json").write_text("old status", encoding="utf-8")
            (root / "index.html").write_text("old page", encoding="utf-8")
            transaction.snapshot(root, backup)
            (root / "data" / "nodes.json").write_text("new nodes", encoding="utf-8")
            (root / "data" / "status.json").write_text("new status", encoding="utf-8")
            (root / "index.html").write_text("new page", encoding="utf-8")

            original_replace = transaction._replace_item_from_snapshot
            failed = []

            def fail_first_item(source, destination):
                if not failed:
                    failed.append(Path(destination).name)
                    raise OSError("simulated first data item restore failure")
                return original_replace(source, destination)

            with patch.object(transaction, "_replace_item_from_snapshot",
                               side_effect=fail_first_item):
                with self.assertRaisesRegex(RuntimeError, "data"):
                    transaction.restore(root, backup)

            self.assertEqual(len(failed), 1)
            other_name = "status.json" if failed[0] == "nodes.json" else "nodes.json"
            self.assertEqual((root / "data" / other_name).read_text(encoding="utf-8"),
                             "old status" if other_name == "status.json" else "old nodes")
            self.assertEqual((root / "index.html").read_text(encoding="utf-8"), "old page")
            self.assertTrue((backup / "data" / failed[0]).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
