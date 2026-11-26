#!/usr/bin/env python3
"""P24 回归：ledger 与 manifest 必须保有完整的必需 artifact 证明。"""
import copy
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager, redirect_stdout
from hashlib import sha256
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_manifest  # noqa: E402
import check_consistency  # noqa: E402
import run_ledger  # noqa: E402

UPDATED = "2026-11-26 10:00"


@contextmanager
def isolated_roots(base):
    data = base / "data"
    with ExitStack() as stack:
        for module, attrs in (
            (run_ledger, {"BASE": str(base), "DATA": str(data),
                          "LEDGER": str(data / "run_ledger.json")}),
            (build_manifest, {"BASE": str(base), "DATA": str(data),
                              "MANIFEST": str(data / "build_manifest.json"),
                              "LEDGER_PATH": str(data / "run_ledger.json")}),
            (check_consistency, {"BASE": str(base), "DATA": str(data),
                                 "MANIFEST": str(data / "build_manifest.json")}),
        ):
            for name, value in attrs.items():
                stack.enter_context(patch.object(module, name, value))
        yield data


def write_offline_outputs(base):
    data = base / "data"
    docs = {
        "data/nodes.json": {
            "schema": 2, "updated": UPDATED, "nodes": [],
            "stats": {"dedup": 0, "subs": {}},
        },
        "data/nodecheck.json": {
            "updated": UPDATED, "summary": {"tested": 0, "ok": 0, "ok_pct": 0},
        },
        "data/picks.json": {"updated": UPDATED, "lists": {}},
        "data/subscriptions.json": {"updated": UPDATED, "groups": []},
        "data/status.json": {"updated": UPDATED},
        "data/node_scores.json": {"updated": UPDATED},
        "data/source_governance.json": {"updated": UPDATED},
        "data/trend.json": {"updated": UPDATED},
    }
    for rel in set(docs) | {
        rel for files in run_ledger.STAGE_FILES.values() for rel in files
    }:
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(docs.get(rel, {})), encoding="utf-8")
    page_data = {
        "updated": UPDATED,
        "stats": {"dedup": 0, "subs": {}},
        "nodes": [],
    }
    (base / "index.html").write_text(
        "<!doctype html>\n<script>\nconst DATA = "
        + json.dumps(page_data, ensure_ascii=False) + ";\n</script>\n",
        encoding="utf-8",
    )


def good_manifest(base):
    artifacts = {}
    for key, rel, _ in build_manifest.ARTIFACTS:
        path = base / rel
        artifacts[key] = {
            "path": rel,
            "sha256": sha256(path.read_bytes()).hexdigest(),
        }
    return {
        "schema_version": build_manifest.SCHEMA_VERSION,
        "build_id": "offline-test",
        "counts": {
            "nodes_total": 0, "dedup": 0, "tested": 0, "real_ok": 0,
            "real_ok_pct": 0, "groups": {}, "picks": {},
        },
        "artifacts": artifacts,
        "pipeline": {
            "run_id": "offline-test", "complete": True,
            "stage_count": len(run_ledger.STAGE_KEYS),
        },
    }


class LedgerArtifactCompletenessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p24-ledger-artifacts-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        write_offline_outputs(self.base)

    def test_empty_artifact_map_is_rejected_for_successful_stage(self):
        with isolated_roots(self.base):
            run_ledger.start("offline-ledger-test")
            for stage in run_ledger.STAGE_KEYS:
                run_ledger.mark(stage)
            doc = run_ledger.load()
            doc["stages"]["detect"]["artifacts"] = {}
            run_ledger.save(doc)

            ok, errs = run_ledger.check()

        self.assertFalse(ok)
        self.assertTrue(any("detect" in e and "缺少必需产物" in e for e in errs), errs)
        self.assertTrue(any("data/nodecheck.json" in e for e in errs), errs)

    def test_unexpected_artifact_path_is_rejected(self):
        with isolated_roots(self.base):
            run_ledger.start("offline-ledger-extra-test")
            for stage in run_ledger.STAGE_KEYS:
                run_ledger.mark(stage)
            doc = run_ledger.load()
            doc["stages"]["detect"]["artifacts"]["data/status.json"] = (
                run_ledger.sha256_file(self.base / "data/status.json"))
            run_ledger.save(doc)

            ok, errs = run_ledger.check()

        self.assertFalse(ok)
        self.assertTrue(any("detect" in e and "未知产物" in e for e in errs), errs)

    def test_malformed_artifact_hash_is_rejected_as_invalid(self):
        with isolated_roots(self.base):
            run_ledger.start("offline-ledger-hash-test")
            for stage in run_ledger.STAGE_KEYS:
                run_ledger.mark(stage)
            doc = run_ledger.load()
            doc["stages"]["detect"]["artifacts"]["data/nodecheck.json"] = "not-a-sha256"
            run_ledger.save(doc)

            ok, errs = run_ledger.check()

        self.assertFalse(ok)
        self.assertTrue(any("data/nodecheck.json" in e and "SHA256" in e for e in errs), errs)


class ManifestArtifactCompletenessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="p24-manifest-artifacts-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        write_offline_outputs(self.base)

    def test_verify_accepts_complete_manifest_and_explicit_missing_compatibility(self):
        with isolated_roots(self.base):
            run_ledger.start("offline-test")
            for stage in run_ledger.STAGE_KEYS:
                run_ledger.mark(stage)
            good, good_errs = build_manifest.verify(good_manifest(self.base))
            self.assertTrue(good, good_errs)

            (self.base / "data/trend.json").unlink()
            missing_manifest = build_manifest.build()
            self.assertTrue(missing_manifest["artifacts"]["trend"].get("missing"))
            rejected, _ = build_manifest.verify(missing_manifest)
            allowed, allowed_errs = build_manifest.verify(
                missing_manifest, expect_missing=True)

        self.assertFalse(rejected)
        self.assertTrue(allowed, allowed_errs)

    def test_verify_rejects_missing_artifact_key(self):
        with isolated_roots(self.base):
            run_ledger.start("offline-test")
            for stage in run_ledger.STAGE_KEYS:
                run_ledger.mark(stage)
            manifest = good_manifest(self.base)
            manifest["artifacts"].pop("trend")

            ok, errs = build_manifest.verify(manifest)

        self.assertFalse(ok)
        self.assertTrue(any("缺少必需 artifact" in e and "trend" in e for e in errs), errs)

    def test_consistency_accepts_complete_current_manifest(self):
        with isolated_roots(self.base) as data:
            (data / "build_manifest.json").write_text(
                json.dumps(good_manifest(self.base)), encoding="utf-8")
            output = StringIO()
            with (
                patch.object(check_consistency, "prev_nodes_count", return_value=0),
                patch.object(sys, "argv", ["check_consistency.py", "--quiet"]),
                redirect_stdout(output),
            ):
                result = check_consistency.main()

        self.assertEqual(result, 0, output.getvalue())

    def test_consistency_rejects_unknown_or_unreadable_manifest(self):
        with isolated_roots(self.base) as data:
            candidates = (
                ("unknown schema", {"schema_version": 99, "artifacts": {}}),
                ("unreadable JSON", "{not-json"),
            )
            for name, invalid in candidates:
                with self.subTest(name=name):
                    path = data / "build_manifest.json"
                    content = invalid if isinstance(invalid, str) else json.dumps(invalid)
                    path.write_text(content, encoding="utf-8")
                    output = StringIO()
                    with (
                        patch.object(check_consistency, "prev_nodes_count", return_value=0),
                        patch.object(sys, "argv", ["check_consistency.py", "--quiet"]),
                        redirect_stdout(output),
                    ):
                        result = check_consistency.main()
                    self.assertEqual(result, 1, (name, output.getvalue()))
                    self.assertIn("manifest", output.getvalue().lower())

    def test_verify_rejects_wrong_path_and_missing_or_malformed_hash(self):
        with isolated_roots(self.base):
            run_ledger.start("offline-test")
            for stage in run_ledger.STAGE_KEYS:
                run_ledger.mark(stage)
            original = good_manifest(self.base)
            cases = (
                ("wrong path", lambda m: m["artifacts"]["trend"].update(path="data/other.json"), "path"),
                ("missing hash", lambda m: m["artifacts"]["trend"].pop("sha256"), "sha256"),
                ("malformed hash", lambda m: m["artifacts"]["trend"].update(sha256="xyz"), "sha256"),
                ("extra key", lambda m: m["artifacts"].update(
                    extra={"path": "data/extra.json", "sha256": "0" * 64}), "extra"),
                ("invalid metadata", lambda m: m["artifacts"].update(trend=None), "trend"),
            )
            failures = []
            for name, mutate, expected in cases:
                with self.subTest(name=name):
                    manifest = copy.deepcopy(original)
                    mutate(manifest)
                    ok, errs = build_manifest.verify(manifest)
                    self.assertFalse(ok, (name, errs))
                    self.assertTrue(any(expected in e for e in errs), (name, errs))
                    failures.extend(errs)

    def test_consistency_rejects_missing_path_or_hash_in_current_manifest(self):
        with isolated_roots(self.base) as data:
            manifest = good_manifest(self.base)
            cases = (
                ("missing key", lambda m: m["artifacts"].pop("trend"), "trend"),
                ("wrong path", lambda m: m["artifacts"]["trend"].update(path="data/other.json"), "path"),
                ("missing hash", lambda m: m["artifacts"]["trend"].pop("sha256"), "sha256"),
                ("malformed hash", lambda m: m["artifacts"]["trend"].update(sha256="xyz"), "sha256"),
                ("extra key", lambda m: m["artifacts"].update(
                    extra={"path": "data/extra.json", "sha256": "0" * 64}), "extra"),
                ("invalid metadata", lambda m: m["artifacts"].update(trend=None), "trend"),
            )
            for name, mutate, expected in cases:
                with self.subTest(name=name):
                    current = copy.deepcopy(manifest)
                    mutate(current)
                    (data / "build_manifest.json").write_text(
                        json.dumps(current), encoding="utf-8")
                    output = StringIO()
                    with (
                        patch.object(check_consistency, "prev_nodes_count", return_value=0),
                        patch.object(sys, "argv", ["check_consistency.py", "--quiet"]),
                        redirect_stdout(output),
                    ):
                        result = check_consistency.main()
                    self.assertEqual(result, 1, (name, output.getvalue()))
                    self.assertIn(expected, output.getvalue(), (name, output.getvalue()))

    def test_consistency_keeps_legacy_missing_or_old_schema_fallback(self):
        with isolated_roots(self.base) as data:
            for old_manifest in (None, {"schema_version": 1, "artifacts": {}}):
                with self.subTest(old_manifest=old_manifest):
                    path = data / "build_manifest.json"
                    if old_manifest is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_text(json.dumps(old_manifest), encoding="utf-8")
                    output = StringIO()
                    with (
                        patch.object(check_consistency, "prev_nodes_count", return_value=0),
                        patch.object(sys, "argv", ["check_consistency.py", "--quiet"]),
                        redirect_stdout(output),
                    ):
                        result = check_consistency.main()
                    self.assertEqual(result, 0, output.getvalue())
                    self.assertIn("旧口径", output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
