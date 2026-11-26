#!/usr/bin/env python3
"""P24 回归：容器核心阶段失败必须停止并恢复上一份可用产物。"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


FAKE_PYTHON = r'''#!/bin/sh
script="$1"
shift
printf '%s %s\n' "$script" "$*" >> "$CALL_LOG"
case "$script" in
  docker/transaction.py)
    if [ "${1:-}" = "restore" ] && [ "${FAIL_RESTORE:-0}" != "0" ]; then
      exit "$FAIL_RESTORE"
    fi
    exec "$REAL_PYTHON" "$APP_ROOT/docker/transaction.py" "$@"
    ;;
  run_ledger.py)
    exec "$REAL_PYTHON" "$APP_ROOT/run_ledger.py" "$@"
    ;;
  netsafe.py)
    exit 0
    ;;
  collector.py)
    if [ "${1:-}" = "--render" ]; then
      printf '{"nodes":[{"addr":"final"}]}' > data/nodes.json
      printf 'new page' > index.html
    else
      printf '{"nodes":[{"addr":"new"}]}' > data/nodes.json
      printf '{"status":"new"}' > data/status.json
    fi
    exit 0
    ;;
  nodecheck.py)
    printf 'new detection' > data/nodecheck.json
    exit "${FAIL_NODECHECK:-0}"
    ;;
  nodescore.py)
    printf 'new score' > data/node_scores.json
    exit 0
    ;;
  governance.py)
    printf 'new governance' > data/source_governance.json
    exit 0
    ;;
  picks.py)
    printf 'new picks' > data/picks.json
    exit 0
    ;;
  export_subs.py)
    printf 'new subscriptions' > data/subscriptions.json
    exit 0
    ;;
  build_manifest.py|audit_privacy.py|check_consistency.py)
    exit 0
    ;;
  -)
    exec "$REAL_PYTHON" - "$@"
    ;;
esac
exit 99
'''


class DockerRunSafetyTest(unittest.TestCase):
    def prepare(self, nodecheck_rc=0, restore_rc=0):
        td = tempfile.TemporaryDirectory(prefix="p24-docker-run-")
        base = Path(td.name)
        app = base / "app"
        (app / "docker").mkdir(parents=True)
        (app / "data" / "logs").mkdir(parents=True)
        (app / "docs").mkdir()
        (app / "export").mkdir()
        shutil.copy2(ROOT / "docker/run.sh", app / "docker/run.sh")
        shutil.copy2(ROOT / "docker/transaction.py", app / "docker/transaction.py")
        shutil.copy2(ROOT / "run_ledger.py", app / "run_ledger.py")
        (app / "data" / "nodes.json").write_text(
            '{"nodes":[{"addr":"old"}]}', encoding="utf-8")
        (app / "data" / "status.json").write_text("old status", encoding="utf-8")
        (app / "data" / "nodecheck.json").write_text("old detection", encoding="utf-8")
        (app / "data" / "logs" / "cron.log").write_text("prior log", encoding="utf-8")
        (app / "index.html").write_text("old page", encoding="utf-8")
        (app / "docs" / "P4-source-quality.md").write_text("old report", encoding="utf-8")
        (app / "docs" / "P19-governance.md").write_text("old governance", encoding="utf-8")
        (app / "export" / "private.txt").write_text("old export", encoding="utf-8")

        stub_dir = base / "bin"
        stub_dir.mkdir()
        stub = stub_dir / "python3"
        stub.write_text(FAKE_PYTHON, encoding="utf-8")
        stub.chmod(0o755)
        calls = base / "calls.log"
        log_dir = base / "run-logs"
        env = os.environ.copy()
        env.update({
            "APP_ROOT": str(app),
            "LOG_DIR": str(log_dir),
            "SKIP_GATE": "1",
            "FAIL_NODECHECK": str(nodecheck_rc),
            "FAIL_RESTORE": str(restore_rc),
            "REAL_PYTHON": sys.executable,
            "CALL_LOG": str(calls),
            "TMPDIR": str(base),
            "PATH": f"{stub_dir}:{env['PATH']}",
        })
        result = subprocess.run(
            ["bash", str(app / "docker/run.sh")], cwd=app, env=env,
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        return td, app, result, calls.read_text(encoding="utf-8") if calls.exists() else ""

    def test_core_stage_failure_stops_pipeline_and_restores_outputs(self):
        td, app, result, calls = self.prepare(nodecheck_rc=7)
        try:
            self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
            self.assertIn("nodecheck.py", calls)
            self.assertNotIn("nodescore.py", calls)
            self.assertEqual(
                (app / "data" / "nodes.json").read_text(encoding="utf-8"),
                '{"nodes":[{"addr":"old"}]}',
            )
            self.assertEqual((app / "data" / "status.json").read_text(), "old status")
            self.assertEqual((app / "data" / "nodecheck.json").read_text(), "old detection")
            self.assertFalse((app / "data" / "run_ledger.json").exists())
            self.assertEqual((app / "index.html").read_text(), "old page")
            self.assertEqual((app / "docs" / "P4-source-quality.md").read_text(), "old report")
            self.assertEqual((app / "docs" / "P19-governance.md").read_text(), "old governance")
            self.assertEqual((app / "export" / "private.txt").read_text(), "old export")
            self.assertEqual((app / "data" / "logs" / "cron.log").read_text(), "prior log")
        finally:
            td.cleanup()

    def test_success_keeps_new_outputs_and_completes_ledger(self):
        td, app, result, calls = self.prepare(nodecheck_rc=0)
        try:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("build_manifest.py --write", calls)
            self.assertIn("audit_privacy.py", calls)
            self.assertIn("check_consistency.py", calls)
            self.assertEqual(
                json.loads((app / "data" / "nodes.json").read_text(encoding="utf-8"))["nodes"][0]["addr"],
                "final",
            )
            ledger = json.loads((app / "data" / "run_ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(set(ledger["stages"]), {
                "collect", "detect", "score", "governance", "picks", "subs", "render",
            })
            self.assertEqual((app / "index.html").read_text(), "new page")
        finally:
            td.cleanup()

    def test_restore_failure_preserves_backup_and_reports_location(self):
        td, app, result, _ = self.prepare(nodecheck_rc=7, restore_rc=9)
        try:
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("快照保留于", result.stdout)
            self.assertIn("best-effort", result.stdout)
            self.assertIn(
                "docker compose run --rm --entrypoint python3 collect docker/transaction.py restore /app",
                result.stdout,
            )
            backups = list(Path(td.name).glob("nodes-output-backup.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o700)
            self.assertEqual(
                (backups[0] / "data" / "nodes.json").read_text(encoding="utf-8"),
                '{"nodes":[{"addr":"old"}]}',
            )
        finally:
            td.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
