#!/usr/bin/env python3
"""Offline regressions for shared primary URLs and multi-source attribution."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import collector  # noqa: E402
import governance  # noqa: E402
import lifecycle  # noqa: E402
import nodecheck  # noqa: E402
import nodescore  # noqa: E402
import sources_io  # noqa: E402


PRIMARY = "https://feed.example.invalid/sub"
MIRROR_1 = "https://mirror-1.example.invalid/sub"
MIRROR_2 = "https://mirror-2.example.invalid/sub"
MIRROR_3 = "https://mirror-3.example.invalid/sub"
SOURCE_A = "source-alpha"
SOURCE_B = "source-beta"
NAME_A = "Alpha"
NAME_B = "Beta"


def source(source_id, name, url=PRIMARY, mirrors=(), enabled=True):
    return {
        "id": source_id, "name": name, "group": "group-1", "url": url,
        "type": "aggregator", "format": "mixed", "enabled": enabled,
        "mirrors": list(mirrors), "license": "MIT", "priority": 0,
    }


def safe_json_load(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError:
        return default


class SharedURLRegistrationTests(unittest.TestCase):
    def test_shared_primary_groups_sources_and_merges_mirrors_in_registration_order(self):
        sources = [
            source(SOURCE_A, NAME_A, mirrors=[MIRROR_1, MIRROR_2]),
            source("disabled", "Disabled", mirrors=["https://ignored.invalid/sub"],
                   enabled=False),
            source(SOURCE_B, NAME_B, mirrors=[MIRROR_2, MIRROR_3]),
            source("source-other", "Other", url="https://other.invalid/sub"),
        ]

        groups = sources_io.collect_groups(sources)

        self.assertEqual(groups, [
            {"url": PRIMARY, "ids": [SOURCE_A, SOURCE_B],
             "names": [NAME_A, NAME_B],
             "urls": [PRIMARY, MIRROR_1, MIRROR_2, MIRROR_3]},
            {"url": "https://other.invalid/sub", "ids": ["source-other"],
             "names": ["Other"], "urls": ["https://other.invalid/sub"]},
        ])

    def test_shared_source_content_is_fetched_once_and_attributed_to_every_id(self):
        sources = [
            source(SOURCE_A, NAME_A, mirrors=[MIRROR_1, MIRROR_2]),
            source(SOURCE_B, NAME_B, mirrors=[MIRROR_2, MIRROR_3]),
        ]
        calls = []
        body = b"trojan://pass@example.com:443#SharedNode\n"

        def fake_fetch(urls, name, use_cache=False):
            calls.append((list(urls), name))
            return body

        with patch.object(collector, "SOURCES_ALL", sources), \
                patch.object(collector, "SOURCES", sources_io.collect_targets(sources)), \
                patch.object(collector, "fetch", side_effect=fake_fetch):
            nodes, stats, errors, raw_total, uniq, rejects = collector.collect()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], [PRIMARY, MIRROR_1, MIRROR_2, MIRROR_3])
        self.assertEqual(errors, [])
        self.assertEqual(raw_total, 2)
        self.assertEqual(uniq, {NAME_A: 0, NAME_B: 0})
        self.assertEqual([row["name"] for row in stats], [NAME_A, NAME_B])
        self.assertEqual([row["count"] for row in stats], [1, 1])
        self.assertEqual(set(rejects), {NAME_A, NAME_B})
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["src"], NAME_A)  # legacy single-source field remains
        self.assertEqual(nodes[0]["src_ids"], [SOURCE_A, SOURCE_B])
        self.assertEqual(nodes[0]["src_names"], [NAME_A, NAME_B])

    def test_duplicate_nodes_from_different_primary_urls_keep_first_owner(self):
        sources = [source(SOURCE_A, NAME_A),
                   source(SOURCE_B, NAME_B, url="https://other.example.invalid/sub")]
        body = b"trojan://pass@example.com:443#SharedNode\n"

        with patch.object(collector, "SOURCES_ALL", sources), \
                patch.object(collector, "SOURCES", sources_io.collect_targets(sources)), \
                patch.object(collector, "fetch", return_value=body):
            nodes, *_ = collector.collect()

        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["src_ids"], [SOURCE_A])
        self.assertEqual(nodes[0]["src_names"], [NAME_A])

    def test_source_stats_count_a_shared_node_for_each_owner_but_not_as_unique(self):
        sources = [source(SOURCE_A, NAME_A), source(SOURCE_B, NAME_B)]
        nodes = [{"src": NAME_A, "src_ids": [SOURCE_A, SOURCE_B],
                  "src_names": [NAME_A, NAME_B]}]

        rows = collector.build_source_stats(
            sources, counts={PRIMARY: 1}, probes={PRIMARY: {"ok": 1, "total": 1}},
            srcstat={PRIMARY: {"code": 200, "ms": 15}}, meta={}, health={}, nodes=nodes,
            uniq={NAME_A: 0, NAME_B: 0}, declared={NAME_A: 1, NAME_B: 1}, real={})

        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id[SOURCE_A]["kept"], 1)
        self.assertEqual(by_id[SOURCE_B]["kept"], 1)
        self.assertEqual(by_id[SOURCE_A]["unique"], 0)
        self.assertEqual(by_id[SOURCE_B]["unique"], 0)

    def test_preferred_export_keeps_nodes_owned_by_any_non_isolated_source(self):
        shared = {"uri": "trojan://shared", "src": NAME_A,
                  "src_ids": [SOURCE_A, SOURCE_B], "src_names": [NAME_A, NAME_B]}
        alpha_only = {"uri": "trojan://alpha", "src": NAME_A,
                      "src_ids": [SOURCE_A], "src_names": [NAME_A]}
        rows = [{"id": SOURCE_A, "name": NAME_A, "state": "isolated"},
                {"id": SOURCE_B, "name": NAME_B, "state": "ok"}]

        selected = collector.preferred_nodes([shared, alpha_only], rows)

        self.assertEqual(selected, [shared])

    def test_source_keys_are_written_for_all_stable_source_ids(self):
        with tempfile.TemporaryDirectory(prefix="shared-source-keys-") as td:
            with patch.object(collector, "OUT_DATA", td), \
                    patch.object(collector, "SOURCE_IDS_BY_URL",
                                 {PRIMARY: [SOURCE_A, SOURCE_B]}, create=True):
                count = collector.write_source_keys({PRIMARY: ["example.com:443"]}, stamp="test")

            doc = json.loads((Path(td) / "source_keys.json").read_text(encoding="utf-8"))

        digest = hashlib.sha256(b"example.com:443").hexdigest()[:16]
        self.assertEqual(count, 2)
        self.assertEqual(doc["sources"], {SOURCE_A: [digest], SOURCE_B: [digest]})


class SharedNodeDownstreamTests(unittest.TestCase):
    def test_lifecycle_freshness_is_attributed_to_every_source_id(self):
        today = 24000
        fp = "0123456789abcdef" + "0" * 16
        node = {"fp": fp, "src": NAME_A,
                "src_ids": [SOURCE_A, SOURCE_B], "src_names": [NAME_A, NAME_B]}
        doc = {"nodes": {fp[:16]: [today - 2, today]}}

        fresh = lifecycle.freshness([node], doc=doc, today=today)

        self.assertEqual(fresh[SOURCE_A]["new7"], 1)
        self.assertEqual(fresh[SOURCE_B]["new7"], 1)
        self.assertEqual(fresh[SOURCE_A]["median_age"], 3)
        self.assertEqual(fresh[SOURCE_B]["median_age"], 3)

    def test_governance_node_overlap_keys_include_every_source_id(self):
        nodes = [{"src": NAME_A, "src_ids": [SOURCE_A, SOURCE_B],
                  "proto": "Trojan", "addr": "example.com:443"}]

        keys = governance.node_keys(nodes)

        expected = {"trojan|example.com:443"}
        self.assertEqual(keys[SOURCE_A], expected)
        self.assertEqual(keys[SOURCE_B], expected)

    def test_governance_prefers_source_id_real_stats_and_keeps_name_keyed_output(self):
        sources = [source(SOURCE_A, NAME_A)]
        stats = {"sources": [{"id": SOURCE_A, "name": NAME_A, "http": 200,
                              "parsed": 10, "kept": 9, "state": "ok"}]}
        scores = {"by_source_id": {SOURCE_A: {"tested": 8, "ok": 6}},
                  "by_source": {NAME_A: {"tested": 4, "ok": 1}}}

        result = governance.per_source(sources, stats, scores, run_id="test-id-map")

        self.assertEqual(result["sources"][NAME_A]["valid_tested"], 8)
        self.assertEqual(result["sources"][NAME_A]["valid_ok"], 6)

    def test_governance_build_normalizes_legacy_name_keys_to_source_ids(self):
        sources = [source(SOURCE_A, NAME_A), source(SOURCE_B, NAME_B)]
        shared_keys = [f"digest-{index}" for index in range(20)]
        docs = {
            governance.STATS_PATH: {"sources": []},
            governance.SCORES_PATH: {"by_source": {}, "by_source_id": {}},
            governance.NODES_PATH: {"nodes": []},
            governance.SCREEN_PATH: {"results": []},
            governance.KEYS_PATH: {"sources": {NAME_A: shared_keys, NAME_B: shared_keys}},
        }

        with patch.object(governance.sources_io, "load", return_value=([], sources, {})), \
                patch.object(governance, "_load",
                             side_effect=lambda path, default: docs.get(path, default)):
            result = governance.build(prev={"baseline": [NAME_A, NAME_B], "sources": {}})

        self.assertEqual(len(result["overlap"]), 1)
        overlap = result["overlap"][0]
        self.assertEqual({overlap["a"], overlap["b"]}, {NAME_A, NAME_B})
        self.assertEqual({overlap["a_id"], overlap["b_id"]}, {SOURCE_A, SOURCE_B})

    def test_nodecheck_samples_a_shared_node_once_after_per_source_selection(self):
        shared = {"fp": "shared-fingerprint", "src": NAME_A,
                  "src_ids": [SOURCE_A, SOURCE_B], "src_names": [NAME_A, NAME_B]}
        alpha_only = {"fp": "alpha-only", "src": NAME_A,
                      "src_ids": [SOURCE_A], "src_names": [NAME_A]}
        beta_only = {"fp": "beta-only", "src": NAME_B,
                     "src_ids": [SOURCE_B], "src_names": [NAME_B]}
        history = {"alpha-only": {"tries": 3, "hits": 3},
                   "beta-only": {"tries": 3, "hits": 3}}

        picked = nodecheck.sample(
            [shared, alpha_only, beta_only], per_src=1, total=10, seed=4,
            history_records=history)

        self.assertEqual([node["fp"] for node in picked], ["shared-fingerprint"])

    def test_nodescore_fans_one_probe_result_out_to_all_source_ids(self):
        fp = "0123456789abcdef" + "f" * 16
        with tempfile.TemporaryDirectory(prefix="shared-node-score-") as td:
            export_dir = Path(td) / "export"
            export_dir.mkdir()
            check_path = Path(td) / "nodecheck.json"
            out_path = Path(td) / "node_scores.json"
            check_path.write_text(json.dumps({
                "updated": "test", "nodes": {fp: {"ok": True, "ms": 100, "cc": "JP"}},
            }), encoding="utf-8")
            (export_dir / "nodes_full.json").write_text(json.dumps([{
                "fp": fp, "src": NAME_A, "src_ids": [SOURCE_A, SOURCE_B],
                "src_names": [NAME_A, NAME_B],
            }]), encoding="utf-8")
            hist = {"nodes": {fp[:16]: {"tries": 1, "hits": 1}}}
            stat = {"seen": 1, "new": 1, "gone": 0, "revived": 0, "total": 1}

            with patch.object(nodescore, "NODECHECK", str(check_path)), \
                    patch.object(nodescore, "EXPORT", str(export_dir)), \
                    patch.object(nodescore, "OUT", str(out_path)), \
                    patch.object(nodescore, "load", side_effect=safe_json_load), \
                    patch.object(nodescore.history, "load", return_value=hist), \
                    patch.object(nodescore.history, "day_of", return_value=24000), \
                    patch.object(nodescore.history, "stability", return_value=("new", "new")), \
                    patch.object(nodescore.history, "prune", return_value=0), \
                    patch.object(nodescore.history, "save"), \
                    patch.object(nodescore.history, "load_trend", return_value={"days": []}), \
                    patch.object(nodescore.history, "trend_add"), \
                    patch.object(nodescore.history, "save_trend"), \
                    patch.object(nodescore.history, "size_kb", return_value=0), \
                    patch.object(nodescore.history, "LEGACY", str(Path(td) / "missing-legacy.json")), \
                    patch.object(nodescore, "update_history", return_value=(hist, stat)), \
                    patch.dict(os.environ, {"NODES_FORCE_WRITE": "1", "GITHUB_ACTIONS": ""}):
                result = nodescore.main()

            saved = json.loads(out_path.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(saved["by_source_id"][SOURCE_A], {"tested": 1, "ok": 1})
        self.assertEqual(saved["by_source_id"][SOURCE_B], {"tested": 1, "ok": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
