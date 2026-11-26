#!/usr/bin/env python3
"""P24 回归：render 可获准更新 nodes.json，但最终哈希/run_id 仍是硬门禁。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import collector  # noqa: E402
import run_ledger  # noqa: E402


class LedgerRenderOwnershipTest(unittest.TestCase):
    def test_render_rewrite_is_explicit_and_final_hash_is_enforced(self):
        with tempfile.TemporaryDirectory(prefix="p24-ledger-render-") as td:
            base = Path(td)
            data_dir = base / "data"
            data_dir.mkdir()
            initial = {
                "schema": 2,
                "updated": "11-25 10:00",
                "ts": 100,
                "raw": 1,
                "errors": [],
                "groups": [],
                "nodes": [{"fp": "old"}],
                "stats": {"dedup": 1, "subs": {}},
            }
            (data_dir / "nodes.json").write_text(
                json.dumps(initial), encoding="utf-8")

            # 填充台账各阶段的占位产物；没有真实采集、代理或网络调用。
            for files in run_ledger.STAGE_FILES.values():
                for rel in files:
                    path = base / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if rel != "data/nodes.json":
                        path.write_text("{}\n", encoding="utf-8")

            rendered_doc = {
                **initial,
                "nodes": [{"fp": "old"}, {"fp": "new"}],
                "stats": {"dedup": 2, "subs": {"all": {"count": 2}}},
            }
            with (
                patch.object(run_ledger, "BASE", str(base)),
                patch.object(run_ledger, "DATA", str(data_dir)),
                patch.object(run_ledger, "LEDGER", str(data_dir / "run_ledger.json")),
                patch.object(collector, "BASE", str(base)),
                patch.object(collector, "OUT_DATA", str(data_dir)),
                patch.object(collector, "SRC_NAME_BY_URL", {}),
                patch.object(collector.sources_io, "load_stats", return_value={"sources": []}),
                patch.object(collector, "assemble", return_value=rendered_doc),
                patch.object(collector, "render_page", side_effect=lambda page: json.dumps(page)),
            ):
                run_ledger.start("p24-render-test")
                run_ledger.mark("collect")
                collect_hash = run_ledger.load()["stages"]["collect"]["artifacts"][
                    "data/nodes.json"]
                for stage in ("detect", "score", "governance", "picks", "subs"):
                    run_ledger.mark(stage)

                collector.rerender()
                final_nodes = json.loads((data_dir / "nodes.json").read_text(encoding="utf-8"))
                self.assertEqual(final_nodes["stats"]["dedup"], 2)
                self.assertNotEqual(run_ledger.sha256_file(data_dir / "nodes.json"), collect_hash)
                page = json.loads((base / "index.html").read_text(encoding="utf-8"))
                self.assertEqual(page["stats"]["dedup"], 2)

                # 改写发生后、render 尚未 mark 时，台账仍必须拒绝发布。
                ok, errs = run_ledger.check()
                self.assertFalse(ok)
                self.assertTrue(any("阶段 render 未标记成功" in e for e in errs), errs)
                self.assertTrue(any("data/nodes.json" in e for e in errs), errs)

                run_ledger.mark("render")
                ok, errs = run_ledger.check()
                self.assertTrue(ok, errs)
                self.assertEqual(
                    run_ledger.load()["stages"]["render"]["artifacts"]["data/nodes.json"],
                    run_ledger.sha256_file(data_dir / "nodes.json"),
                )
                self.assertEqual(
                    run_ledger.load()["stages"]["render"]["artifacts"]["index.html"],
                    run_ledger.sha256_file(base / "index.html"),
                )

                # render 的跨轮记录不能借授权覆写绕过 run_id 校验。
                ledger = run_ledger.load()
                ledger["stages"]["render"]["run_id"] = "another-run"
                run_ledger.save(ledger)
                ok, errs = run_ledger.check()
                self.assertFalse(ok)
                self.assertTrue(any("阶段 render 的 run_id" in e for e in errs), errs)
                ledger["stages"]["render"]["run_id"] = "p24-render-test"
                run_ledger.save(ledger)

                # 其他 collect 产物没有 render 授权，不能因同阶段存在共享文件而放行。
                status_path = data_dir / "status.json"
                status_before = status_path.read_text(encoding="utf-8")
                status_path.write_text('{"tampered":true}\n', encoding="utf-8")
                ok, errs = run_ledger.check()
                self.assertFalse(ok)
                self.assertTrue(any("data/status.json" in e for e in errs), errs)
                status_path.write_text(status_before, encoding="utf-8")

                # 最终 mark 后任何节点快照改动仍会被拦下。
                (data_dir / "nodes.json").write_text(
                    json.dumps({"tampered": True}), encoding="utf-8")
                ok, errs = run_ledger.check()
                self.assertFalse(ok)
                self.assertTrue(any("data/nodes.json" in e and "被改动" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
