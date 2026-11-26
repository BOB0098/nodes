#!/usr/bin/env python3
"""P24 回归：本地 cron 包装器必须保留 collector 的退出状态。"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RunDailyExitTest(unittest.TestCase):
    def run_with_stub(self, exit_code):
        td = tempfile.TemporaryDirectory(prefix="p24-run-daily-")
        base = Path(td.name)
        script = base / "run_daily.sh"
        shutil.copy2(ROOT / "run_daily.sh", script)
        stub_dir = base / "bin"
        stub_dir.mkdir()
        stub = stub_dir / "python3"
        stub.write_text(
            "#!/bin/sh\n"
            "[ \"$1\" = collector.py ] || exit 99\n"
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{stub_dir}:{env['PATH']}"
        result = subprocess.run(
            ["bash", str(script)], cwd=base, env=env, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        return td, result, (base / "logs.txt").read_text(encoding="utf-8")

    def test_failure_status_and_log_are_preserved(self):
        td, result, log = self.run_with_stub(7)
        try:
            self.assertEqual(result.returncode, 7)
            self.assertIn("失败（rc=7）", log)
            self.assertNotIn("完成", log)
        finally:
            td.cleanup()

    def test_success_status_is_logged(self):
        td, result, log = self.run_with_stub(0)
        try:
            self.assertEqual(result.returncode, 0)
            self.assertIn("完成", log)
            self.assertNotIn("失败", log)
        finally:
            td.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
