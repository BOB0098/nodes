#!/usr/bin/env python3
"""P24 回归：故障演练只改隔离副本，异常中断不影响正式产物。"""
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import drill  # noqa: E402
import drill_publish  # noqa: E402


class DrillActionsIsolationTest(unittest.TestCase):
    def test_actions_failure_does_not_move_formal_export_or_change_nodecheck(self):
        with tempfile.TemporaryDirectory(prefix="p24-drill-actions-") as td:
            app = Path(td)
            (app / "data").mkdir()
            (app / "export").mkdir()
            nodecheck_path = app / "data/nodecheck.json"
            full_path = app / "export/nodes_full.json"
            nodecheck_path.write_bytes(b"formal nodecheck sentinel")
            full_path.write_bytes(b"formal full-export sentinel")
            before_nodecheck = nodecheck_path.read_bytes()
            before_full = full_path.read_bytes()
            shutil.copy2(ROOT / "nodecheck.py", app / "nodecheck.py")
            shutil.copy2(ROOT / "netsafe.py", app / "netsafe.py")

            with (
                patch.object(drill, "BASE", str(app)),
                patch.object(drill, "NODECHECK", str(nodecheck_path)),
                patch.object(drill, "FULL", str(full_path)),
                patch.object(drill.os, "replace", wraps=os.replace) as replace,
            ):
                passed, detail = drill.drill_actions_fail()

            self.assertTrue(passed, detail)
            self.assertEqual(nodecheck_path.read_bytes(), before_nodecheck)
            self.assertEqual(full_path.read_bytes(), before_full)
            formal_paths = {str(full_path), str(nodecheck_path)}
            touched = [
                call for call in replace.call_args_list
                if any(os.path.abspath(os.fspath(arg)) in formal_paths
                       for arg in call.args[:2])
            ]
            self.assertEqual(touched, [])


class DrillPublishIsolationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p24-drill-publish-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / "app"
        (self.base / "data").mkdir(parents=True)
        (self.base / "docs").mkdir()
        self.artifacts = [
            ("index_html", "index.html", "page"),
            ("nodes", "data/nodes.json", "nodes"),
            ("nodecheck", "data/nodecheck.json", "check"),
            ("scores", "data/scores.json", "scores"),
            ("picks", "data/picks.json", "picks"),
        ]
        for _, rel, content in self.artifacts:
            path = self.base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if rel == "data/nodes.json":
                path.write_text(
                    json.dumps({"stats": {"dedup": 1}}), encoding="utf-8")
            else:
                path.write_text(content, encoding="utf-8")
        (self.base / "data/build_manifest.json").write_text(
            json.dumps({"artifacts": {
                key: {"path": rel} for key, rel, _ in self.artifacts
            }}), encoding="utf-8")
        for rel in ("check_consistency.py", "build_manifest.py", "run_ledger.py"):
            (self.base / rel).write_text(
                "# isolated test fixture\n", encoding="utf-8")

    def _patch_main(self, run_check, output):
        stack = ExitStack()
        stack.enter_context(patch.object(drill_publish, "BASE", str(self.base)))
        stack.enter_context(patch.object(
            drill_publish, "MANIFEST",
            str(self.base / "data/build_manifest.json")))
        stack.enter_context(patch.object(
            drill_publish, "CHECK", str(self.base / "check_consistency.py")))
        stack.enter_context(patch.object(
            drill_publish, "ARTIFACTS", self.artifacts, create=True))
        stack.enter_context(patch.object(
            drill_publish, "run_check", side_effect=run_check))
        stack.enter_context(patch.object(sys, "argv", ["drill_publish.py", "--quiet"]))
        stack.enter_context(redirect_stdout(output))
        return stack

    def test_publish_drill_runs_checks_only_in_temporary_workspace(self):
        roots = []
        source_nodes = (self.base / "data/nodes.json").read_bytes()

        def inspect_isolated(root=None):
            candidate = Path(root) if root is not None else self.base
            roots.append(candidate)
            doc = json.loads(
                (candidate / "data/nodes.json").read_text(encoding="utf-8"))
            if doc["stats"]["dedup"] != 1:
                return 1, "data/nodes.json 与 manifest 哈希不一致"
            return 0, ""

        output = StringIO()
        with self._patch_main(inspect_isolated, output):
            result = drill_publish.main()

        self.assertEqual(result, 0, output.getvalue())
        self.assertTrue(roots)
        self.assertTrue(all(root != self.base for root in roots), roots)
        self.assertEqual(len(set(roots)), 1, roots)
        self.assertEqual((self.base / "data/nodes.json").read_bytes(), source_nodes)

    def test_publish_drill_interruption_cannot_leave_formal_nodes_tampered(self):
        source_nodes = (self.base / "data/nodes.json").read_bytes()
        calls = 0
        roots = []

        def interrupt_after_fault(root=None):
            nonlocal calls
            calls += 1
            candidate = Path(root) if root is not None else self.base
            roots.append(candidate)
            if calls == 2:
                raise KeyboardInterrupt("injected interruption")
            return 0, ""

        output = StringIO()
        with self._patch_main(interrupt_after_fault, output):
            with self.assertRaises(KeyboardInterrupt):
                drill_publish.main()

        self.assertEqual((self.base / "data/nodes.json").read_bytes(), source_nodes)
        self.assertTrue(roots)
        self.assertTrue(all(root != self.base for root in roots), roots)

    def test_sigkill_after_tampering_leaves_formal_nodes_unchanged(self):
        source_nodes = (self.base / "data/nodes.json").read_bytes()
        temp_root = self.base / "tmp"
        temp_root.mkdir()
        child = textwrap.dedent(
            """
            import os, signal, sys
            from pathlib import Path
            from unittest.mock import patch
            sys.path.insert(0, sys.argv[1])
            import drill_publish
            base = Path(sys.argv[2])
            artifacts = [
                ("index_html", "index.html", "page"),
                ("nodes", "data/nodes.json", "nodes"),
                ("nodecheck", "data/nodecheck.json", "check"),
                ("scores", "data/scores.json", "scores"),
                ("picks", "data/picks.json", "picks"),
            ]
            calls = 0
            def kill_after_injection(root=None):
                global calls
                calls += 1
                if calls == 2:
                    os.kill(os.getpid(), signal.SIGKILL)
                return 0, ""
            with (
                patch.object(drill_publish, "BASE", str(base)),
                patch.object(drill_publish, "MANIFEST", str(base / "data/build_manifest.json")),
                patch.object(drill_publish, "CHECK", str(base / "check_consistency.py")),
                patch.object(drill_publish, "ARTIFACTS", artifacts),
                patch.object(drill_publish, "run_check", side_effect=kill_after_injection),
                patch.object(sys, "argv", ["drill_publish.py", "--quiet"]),
            ):
                drill_publish.main()
            """
        )
        env = os.environ.copy()
        env["TMPDIR"] = str(temp_root)
        result = subprocess.run(
            [sys.executable, "-c", child, str(ROOT), str(self.base)],
            capture_output=True, text=True, env=env, check=False,
        )

        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        self.assertEqual((self.base / "data/nodes.json").read_bytes(), source_nodes)
        interrupted = list(temp_root.glob("drill-publish-*"))
        self.assertEqual(len(interrupted), 1, interrupted)
        changed_copy = json.loads(
            (interrupted[0] / "data/nodes.json").read_text(encoding="utf-8"))
        self.assertEqual(changed_copy["stats"]["dedup"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
