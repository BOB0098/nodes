import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import governance


class GovernanceRoundIdentityTests(unittest.TestCase):
    source_name = "new-source"
    sources = [{"id": "new-source", "name": source_name, "enabled": True}]
    scores = {"by_source": {}}

    def make_stats(self, *, checked_at="11-26 11:10", http=200, parsed=10):
        return {
            "sources": [{
                "name": self.source_name,
                "http": http,
                "parsed": parsed,
                "kept": parsed,
                "dup_rate": 0.0,
                "fails_streak": 0,
                "fails_total": 0,
                "checked_at": checked_at,
                "state": "ok",
            }]
        }

    def build_round(self, prev, run_id, **stats_kwargs):
        with patch.dict(os.environ, {"GITHUB_RUN_ID": run_id}, clear=False):
            return governance.per_source(
                self.sources, self.make_stats(**stats_kwargs), self.scores, prev)

    def initial_previous(self, *, rounds=None, rounds_seen=0):
        return {
            "baseline": ["legacy-source"],
            "sources": {
                self.source_name: {
                    "rounds_seen": rounds_seen,
                    "rounds": list(rounds or []),
                }
            },
        }

    def test_same_workflow_run_retry_replaces_its_round_and_counts_once(self):
        first = self.build_round(self.initial_previous(), "run-101")
        retried = self.build_round(first, "run-101")

        source = retried["sources"][self.source_name]
        self.assertEqual(len(source["rounds"]), 1)
        self.assertEqual(source["rounds_seen"], 1)
        self.assertEqual(source["fetch_success_pct"], 100.0)
        self.assertEqual(source["rounds"][0].get("run_id"), "run-101")

    def test_retry_with_updated_input_replaces_not_duplicates_the_logical_round(self):
        first = self.build_round(self.initial_previous(), "run-101")
        retried = self.build_round(
            first, "run-101", checked_at="11-26 11:12", http=503, parsed=None)

        source = retried["sources"][self.source_name]
        self.assertEqual(len(source["rounds"]), 1)
        self.assertEqual(source["rounds_seen"], 1)
        self.assertEqual(source["rounds"][0]["ts"], "11-26 11:12")
        self.assertFalse(source["rounds"][0]["ok"])
        self.assertEqual(source["fetch_success_pct"], 0.0)
        self.assertIsNone(source["last_success"])

    def test_distinct_workflow_runs_with_same_checked_at_are_distinct_rounds(self):
        first = self.build_round(self.initial_previous(), "run-101")
        second = self.build_round(first, "run-102")

        source = second["sources"][self.source_name]
        self.assertEqual(len(source["rounds"]), 2)
        self.assertEqual(source["rounds_seen"], 2)
        self.assertEqual([r.get("run_id") for r in source["rounds"]], ["run-101", "run-102"])
        self.assertEqual(source["rounds"][0]["ts"], source["rounds"][1]["ts"])

    def test_nonadjacent_retry_updates_the_matching_round_without_counting_again(self):
        run_a = self.build_round(self.initial_previous(), "run-A")
        run_b = self.build_round(run_a, "run-B", http=503, parsed=None)
        retried_a = self.build_round(
            run_b, "run-A", checked_at="11-26 11:12", parsed=20)

        source = retried_a["sources"][self.source_name]
        self.assertEqual(source["rounds_seen"], 2)
        self.assertEqual(len(source["rounds"]), 2)
        self.assertEqual([r.get("run_id") for r in source["rounds"]], ["run-B", "run-A"])
        self.assertEqual(source["rounds"][-1]["parsed"], 20)

    def test_legacy_round_without_run_id_is_not_back_matched_by_timestamp(self):
        prev = self.initial_previous(
            rounds=[{"ts": "11-26 11:10", "ok": True, "http": 200, "parsed": 10}],
            rounds_seen=2)
        current = self.build_round(prev, "run-101")

        source = current["sources"][self.source_name]
        self.assertEqual(len(source["rounds"]), 2)
        self.assertEqual(source["rounds_seen"], 3)
        self.assertNotIn("run_id", source["rounds"][0])
        self.assertEqual(source["rounds"][1].get("run_id"), "run-101")

    def test_retry_of_round_evicted_from_30_item_window_does_not_recount_or_reappend(self):
        current = self.initial_previous()
        for i in range(31):
            current = self.build_round(current, f"run-{i}",
                                       checked_at=f"11-26 11:{i:02d}")

        retried_oldest = self.build_round(
            current, "run-0", checked_at="11-26 12:00", parsed=99)
        source = retried_oldest["sources"][self.source_name]

        self.assertEqual(source["rounds_seen"], 31)
        self.assertEqual(len(source["rounds"]), governance.WINDOW)
        self.assertNotIn("run-0", [r.get("run_id") for r in source["rounds"]])


if __name__ == "__main__":
    unittest.main()
