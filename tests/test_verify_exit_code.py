#!/usr/bin/env python3
"""P24 J7 regression: verify.sh must report the failed command's real exit code."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class VerifyExitCodeTest(unittest.TestCase):
    def run_verify(self, fail_netsafe):
        with tempfile.TemporaryDirectory(prefix="p24-verify-py-") as td:
            python = Path(td) / "fake-python"
            python.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "args = sys.argv[1:]\n"
                "if args == ['--version']:\n"
                "    print('Python 3.11.9')\n"
                "elif args[:1] == ['-c']:\n"
                "    raise SystemExit(0)\n"
                "elif args and args[0] == 'netsafe.py' and "
                + repr(fail_netsafe)
                + ":\n    raise SystemExit(7)\n"
                "else:\n    raise SystemExit(0)\n",
                encoding="utf-8",
            )
            python.chmod(0o755)
            env = os.environ.copy()
            env["PY"] = str(python)
            return subprocess.run(
                ["bash", str(ROOT / "verify.sh"), "--quick"],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

    def test_failure_reports_actual_exit_code_not_counter_status(self):
        result = self.run_verify(True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL] 安全边界自测 (netsafe) (退出码 7)", result.stdout)
        self.assertIn("结果：", result.stdout)
        self.assertIn("项失败", result.stdout)

    def test_success_path_still_returns_zero(self):
        result = self.run_verify(False)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("→ 发布门槛通过", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
