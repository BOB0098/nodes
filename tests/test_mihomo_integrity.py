#!/usr/bin/env python3
"""P24 回归：只执行与固定可信压缩资产一致的 mihomo 二进制。"""
import gzip
import hashlib
import os
import random
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import clienttest  # noqa: E402
import nodecheck  # noqa: E402


class MihomoIntegrityTest(unittest.TestCase):
    def test_verified_cached_archive_rebuilds_untrusted_executable(self):
        payload = random.Random(20261126).randbytes(4 << 20)
        archive_bytes = gzip.compress(payload, mtime=0)
        expected_digest = hashlib.sha256(archive_bytes).hexdigest()
        self.assertGreaterEqual(len(archive_bytes), 4 << 20)
        with tempfile.TemporaryDirectory(prefix="p24-mihomo-cache-") as td:
            binary = Path(td) / "mihomo"
            archive = Path(str(binary) + ".gz")
            binary.write_bytes(b"untrusted cached executable")
            binary.chmod(0o755)
            archive.write_bytes(archive_bytes)
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                if command[0] == "gunzip":
                    self.assertEqual(command[1], "-c")
                    kwargs["stdout"].write(payload)
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                if command[-1] == "-v":
                    return SimpleNamespace(returncode=0, stdout="mihomo v1.19.34", stderr="")
                raise AssertionError(f"unexpected command: {command}")

            with (
                patch.object(nodecheck, "CACHE", td),
                patch.object(nodecheck, "MIHOMO", str(binary)),
                patch.object(nodecheck, "MIHOMO_SHA256", expected_digest, create=True),
                patch.object(nodecheck, "mihomo_cache_lock",
                             wraps=nodecheck.mihomo_cache_lock) as cache_lock,
                patch.object(nodecheck.subprocess, "run", side_effect=fake_run),
            ):
                result = nodecheck.ensure_mihomo()

            self.assertEqual(result, str(binary))
            self.assertEqual(binary.read_bytes(), payload)
            self.assertNotIn("curl", [command[0] for command in commands])
            self.assertEqual(commands[0][:2], ["gunzip", "-c"])
            self.assertEqual(commands[1][1:], ["-v"])
            self.assertTrue(commands[1][0].startswith("/proc/self/fd/"))
            cache_lock.assert_called_once_with()

    def test_download_digest_mismatch_is_rejected_before_unpack_or_execution(self):
        with tempfile.TemporaryDirectory(prefix="p24-mihomo-download-") as td:
            binary = Path(td) / "mihomo"
            commands = []

            def fake_run(command, **kwargs):
                commands.append(command)
                if command[0] == "curl":
                    output = Path(command[command.index("-o") + 1])
                    output.write_bytes(b"x" * (4 << 20))
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                return SimpleNamespace(returncode=0, stdout="mihomo fake", stderr="")

            with (
                patch.object(nodecheck, "CACHE", td),
                patch.object(nodecheck, "MIHOMO", str(binary)),
                patch.object(nodecheck, "MIHOMO_URLS", [
                    "https://mirror.invalid/mihomo.gz",
                    "https://release.invalid/mihomo.gz",
                ]),
                patch.object(nodecheck, "MIHOMO_SHA256", "0" * 64, create=True),
                patch.object(nodecheck.subprocess, "run", side_effect=fake_run),
            ):
                with self.assertRaisesRegex(RuntimeError, "mihomo 下载失败"):
                    nodecheck.ensure_mihomo()

            invoked = [command[0] for command in commands]
            self.assertEqual(invoked, ["curl", "curl"])
            self.assertFalse(binary.exists())

    def test_concurrent_downloads_use_unique_temporary_paths(self):
        barrier = Barrier(2)
        paths = []
        paths_lock = Lock()

        def fake_run(command, **kwargs):
            temporary = command[command.index("-o") + 1]
            with paths_lock:
                paths.append(temporary)
            barrier.wait(timeout=5)
            Path(temporary).write_bytes(b"verified archive")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory(prefix="p24-mihomo-parallel-") as td:
            archive = Path(td) / "mihomo.gz"
            with (
                patch.object(nodecheck, "MIHOMO_URLS", ["https://release.invalid/mihomo.gz"]),
                patch.object(nodecheck, "_verified_mihomo_archive", return_value=True),
                patch.object(nodecheck.subprocess, "run", side_effect=fake_run),
            ):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(nodecheck._download_mihomo_archive, str(archive))
                               for _ in range(2)]
                    for future in futures:
                        future.result(timeout=10)
            self.assertTrue(archive.exists())

        self.assertEqual(len(paths), 2)
        self.assertEqual(len(set(paths)), 2)

    def test_mihomo_cache_lock_serializes_competing_threads(self):
        binary_path = ""
        first_entered = Event()
        second_started = Event()
        second_entered = Event()
        release_first = Event()

        def first():
            with nodecheck.mihomo_cache_lock(binary_path):
                first_entered.set()
                if not release_first.wait(timeout=5):
                    raise TimeoutError("test did not release first cache lock")

        def second():
            second_started.set()
            with nodecheck.mihomo_cache_lock(binary_path):
                second_entered.set()

        with tempfile.TemporaryDirectory(prefix="p24-mihomo-lock-") as td:
            binary_path = str(Path(td) / "mihomo")
            with ThreadPoolExecutor(max_workers=2) as pool:
                first_future = pool.submit(first)
                self.assertTrue(first_entered.wait(timeout=2))
                second_future = pool.submit(second)
                self.assertTrue(second_started.wait(timeout=2))
                try:
                    self.assertFalse(second_entered.wait(timeout=0.1))
                finally:
                    release_first.set()
                first_future.result(timeout=2)
                second_future.result(timeout=2)
            self.assertTrue(second_entered.is_set())

    def test_clienttest_uses_verified_mihomo_runner(self):
        with tempfile.TemporaryDirectory(prefix="p24-clienttest-verified-runner-") as td:
            binary = Path(td) / "mihomo"
            binary.write_bytes(b"test executable")
            binary.chmod(0o755)
            bins = dict(clienttest.BINS)
            bins["mihomo"] = str(binary)
            with (
                patch.object(clienttest, "BINS", bins),
                patch.object(nodecheck, "verified_cached_mihomo", return_value=True),
                patch.object(nodecheck, "run_mihomo",
                             return_value=SimpleNamespace(returncode=0,
                                                          stdout="configuration successful",
                                                          stderr=""), create=True) as secure_run,
                patch.object(clienttest, "run",
                             return_value=(0, "configuration successful", 0.0)) as legacy_run,
            ):
                result = clienttest.check_mihomo("/unused/subscription.yaml")

        self.assertEqual(result[0], "pass")
        secure_run.assert_called_once()
        legacy_run.assert_not_called()

    def test_verified_runner_passes_open_descriptor_to_subprocess(self):
        result = SimpleNamespace(returncode=0, stdout="mihomo version", stderr="")
        with (
            patch.object(nodecheck, "verified_mihomo_executable",
                         return_value=nullcontext(("/proc/self/fd/37", 37)), create=True) as opened,
            patch.object(nodecheck.subprocess, "run", return_value=result) as run,
        ):
            actual = nodecheck.run_mihomo(["-v"], binary_path="/tmp/mihomo", timeout=10)

        opened.assert_called_once_with("/tmp/mihomo")
        self.assertIs(actual, result)
        command = run.call_args.args[0]
        self.assertEqual(command, ["/proc/self/fd/37", "-v"])
        self.assertEqual(run.call_args.kwargs["pass_fds"], (37,))

    def test_verified_popen_passes_open_descriptor_to_child(self):
        process = SimpleNamespace(pid=123)
        with (
            patch.object(nodecheck, "verified_mihomo_executable",
                         return_value=nullcontext(("/proc/self/fd/41", 41)), create=True),
            patch.object(nodecheck.subprocess, "Popen", return_value=process) as popen,
        ):
            actual = nodecheck.popen_mihomo(["-f", "config.yaml"],
                                           stdout=subprocess.DEVNULL)

        self.assertIs(actual, process)
        self.assertEqual(popen.call_args.args[0], ["/proc/self/fd/41", "-f", "config.yaml"])
        self.assertEqual(popen.call_args.kwargs["pass_fds"], (41,))

    def test_open_verified_descriptor_survives_path_replacement(self):
        payload = random.Random(20261127).randbytes(4 << 20)
        archive_bytes = gzip.compress(payload, mtime=0)
        expected_digest = hashlib.sha256(archive_bytes).hexdigest()
        with tempfile.TemporaryDirectory(prefix="p24-mihomo-fd-") as td:
            binary = Path(td) / "mihomo"
            archive = Path(str(binary) + ".gz")
            binary.write_bytes(payload)
            binary.chmod(0o755)
            archive.write_bytes(archive_bytes)
            with patch.object(nodecheck, "MIHOMO_SHA256", expected_digest, create=True):
                with nodecheck.verified_mihomo_executable(str(binary)) as (exec_path, fd):
                    self.assertEqual(exec_path, f"/proc/self/fd/{fd}")
                    binary.unlink()
                    binary.write_bytes(b"replaced after verification")
                    self.assertEqual(os.pread(fd, len(payload), 0), payload)

    def test_unverified_executable_is_not_used_when_archive_is_missing(self):
        with tempfile.TemporaryDirectory(prefix="p24-mihomo-unverified-") as td:
            binary = Path(td) / "mihomo"
            binary.write_bytes(b"untrusted cached executable")
            binary.chmod(0o755)
            with (
                patch.object(nodecheck, "CACHE", td),
                patch.object(nodecheck, "MIHOMO", str(binary)),
                patch.object(nodecheck, "MIHOMO_URLS", ["https://release.invalid/mihomo.gz"]),
                patch.object(nodecheck.subprocess, "run", side_effect=OSError("offline")) as run,
            ):
                with self.assertRaisesRegex(RuntimeError, "mihomo 下载失败"):
                    nodecheck.ensure_mihomo()

            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][0], "curl")

    def test_clienttest_refuses_to_execute_unverified_mihomo_cache(self):
        with tempfile.TemporaryDirectory(prefix="p24-clienttest-mihomo-") as td:
            binary = Path(td) / "mihomo"
            binary.write_bytes(b"untrusted cached executable")
            binary.chmod(0o755)
            bins = dict(clienttest.BINS)
            bins["mihomo"] = str(binary)
            with (
                patch.object(clienttest, "BINS", bins),
                patch.object(nodecheck, "MIHOMO", str(binary)),
                patch.object(nodecheck, "MIHOMO_SHA256", "0" * 64, create=True),
                patch.object(clienttest, "run", return_value=(0, "configuration successful", 0.0)) as run,
            ):
                result = clienttest.check_mihomo("/unused/subscription.yaml")

            self.assertEqual(result[0], "fail")
            self.assertIn("完整性", result[1])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
