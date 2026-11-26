import os
import sys
import unittest
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lifecycle


class LifecycleRegressionTests(unittest.TestCase):
    FP_A = "a" * 32
    FP_B = "b" * 32
    FP_C = "c" * 32

    @staticmethod
    def node(fp, src="feed"):
        return {"fp": fp, "src": src}

    def test_day_of_changes_at_cst_midnight_not_utc_midnight(self):
        before = datetime(2026, 11, 25, 15, 59, tzinfo=timezone.utc)
        after = datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc)
        day = (date(2026, 11, 25) - date(1970, 1, 1)).days

        self.assertEqual(lifecycle.day_of(before), day)
        self.assertEqual(lifecycle.day_of(after), day + 1)

    def test_consecutive_collection_days_are_not_a_return_but_a_gap_is(self):
        today = 21000
        doc, first = lifecycle.update(
            [self.node(self.FP_A)], doc={"nodes": {}}, today=today)
        self.assertEqual(first["new"], 1)

        doc, next_day = lifecycle.update(
            [self.node(self.FP_A)], doc=doc, today=today + 1)
        self.assertEqual(next_day["back"], 0)

        doc, after_gap = lifecycle.update(
            [self.node(self.FP_A)], doc=doc, today=today + 3)
        self.assertEqual(after_gap["back"], 1)
        self.assertEqual(doc["nodes"][self.FP_A[:16]], [today, today + 3])

    def test_annotate_reports_inclusive_age_and_missing_record_as_unknown(self):
        today = 21500
        doc = {"nodes": {self.FP_A[:16]: [today - 4, today]}}
        current = {"fp": self.FP_A}
        missing = {"fp": self.FP_B}

        result = lifecycle.annotate([current, missing], doc=doc, today=today)

        self.assertEqual(result[0]["first_day"], today - 4)
        self.assertEqual(result[0]["last_day"], today)
        self.assertEqual(result[0]["age_days"], 5)
        self.assertIsNone(result[1]["age_days"])

    def test_reappearance_after_45_days_starts_a_new_lifecycle(self):
        today = 22000
        doc = {"nodes": {self.FP_A[:16]: [today - 45, today - 45]}}

        doc, stat = lifecycle.update(
            [self.node(self.FP_A)], doc=doc, today=today)

        self.assertEqual(stat["new"], 1)
        self.assertEqual(stat["back"], 0)
        self.assertEqual(doc["nodes"][self.FP_A[:16]], [today, today])

    def test_expiry_boundary_is_45_elapsed_days_and_gone_excludes_expired(self):
        today = 23000
        doc = {"nodes": {
            self.FP_A[:16]: [today - 60, today - 45],
            self.FP_B[:16]: [today - 60, today - 44],
        }}

        doc, stat = lifecycle.update([], doc=doc, today=today)

        self.assertNotIn(self.FP_A[:16], doc["nodes"])
        self.assertIn(self.FP_B[:16], doc["nodes"])
        self.assertEqual(stat["gone"], 1)
        self.assertEqual(stat["total"], 1)

    def test_freshness_measures_collection_age_with_inclusive_seven_day_range(self):
        today = 24000
        doc = {"nodes": {
            self.FP_A[:16]: [today - 6, today],
            self.FP_B[:16]: [today - 7, today],
            self.FP_C[:16]: [today - 20, today],
        }}
        # These are collected-node records; health-check history is a separate input/domain.
        nodes = [self.node(self.FP_A), self.node(self.FP_B), self.node(self.FP_C)]

        fresh = lifecycle.freshness(nodes, doc=doc, today=today)

        self.assertEqual(fresh["feed"]["new7"], 1)
        self.assertEqual(fresh["feed"]["median_age"], 8)
        self.assertEqual(fresh["feed"]["max_age"], 21)
        self.assertEqual(fresh["_span"], 21)

    def test_cold_start_span_counts_the_first_collection_day(self):
        today = 25000
        doc = {"nodes": {self.FP_A[:16]: [today - 5, today]}}

        fresh = lifecycle.freshness(
            [self.node(self.FP_A)], doc=doc, today=today)

        self.assertEqual(fresh["_span"], 6)
        self.assertEqual(fresh["feed"]["new7"], 1)

    def test_freshness_empty_input_has_no_source_metrics_or_tracked_span(self):
        fresh = lifecycle.freshness([], doc={"nodes": {}}, today=26000)

        self.assertEqual(fresh, {"_span": 0})


if __name__ == "__main__":
    unittest.main()
