import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import threshold_watch


class ThresholdWatchIdempotencyTests(unittest.TestCase):
    def _append_sequence(self, observations):
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "threshold_watch.json")
            with patch.object(threshold_watch, "OUT", output), \
                    patch.object(threshold_watch, "observe",
                                 side_effect=observations):
                results = [threshold_watch.append_observation(force=True)
                           for _ in observations]
            return results

    def test_nonadjacent_repeat_of_same_input_fingerprint_is_not_appended(self):
        t1 = {"ts": "2026-11-25 10:00", "total": 100, "best_n": 20,
              "snapshot_id": "input-T1"}
        t2 = {"ts": "2026-11-26 10:00", "total": 110, "best_n": 21,
              "snapshot_id": "input-T2"}

        results = self._append_sequence([t1, t2, t1])

        self.assertEqual([added for _, added in results], [True, True, False])
        self.assertEqual(results[-1][0]["rows"], [t1, t2])
        self.assertEqual(threshold_watch.distinct_days(results[-1][0]), 2)

    def test_different_input_fingerprints_with_same_timestamp_are_both_kept(self):
        first = {"ts": "2026-11-25 10:00", "total": 100, "best_n": 20,
                 "snapshot_id": "input-A"}
        second = {"ts": "2026-11-25 10:00", "total": 101, "best_n": 21,
                  "snapshot_id": "input-B"}

        results = self._append_sequence([first, second])

        self.assertEqual([added for _, added in results], [True, True])
        self.assertEqual(results[-1][0]["rows"], [first, second])
        # 轮数可以多于一天数；时间窗仍按 ts 的自然日统计。
        self.assertEqual(threshold_watch.distinct_days(results[-1][0]), 1)

    def test_legacy_rows_without_fingerprint_are_preserved_without_fallback_match(self):
        legacy = {"ts": "2026-11-25 10:00", "total": 100, "best_n": 20}
        current = {**legacy, "snapshot_id": "new-input-fingerprint"}
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "threshold_watch.json")
            original = {"schema_version": 1, "days_needed": 7,
                        "rows": [legacy]}
            with open(output, "w", encoding="utf-8") as handle:
                json.dump(original, handle)
            with patch.object(threshold_watch, "OUT", output), \
                    patch.object(threshold_watch, "observe", return_value=current):
                first_doc, first_added = threshold_watch.append_observation(force=True)
                second_doc, second_added = threshold_watch.append_observation(force=True)

        self.assertTrue(first_added)
        self.assertFalse(second_added)
        self.assertEqual(first_doc["rows"], [legacy, current])
        self.assertEqual(second_doc["rows"], [legacy, current])

    def test_observation_fingerprint_is_stable_for_same_files_and_changes_with_input(self):
        with tempfile.TemporaryDirectory() as directory:
            scores_path = os.path.join(directory, "node_scores.json")
            picks_path = os.path.join(directory, "picks.json")
            with open(scores_path, "w", encoding="utf-8") as handle:
                json.dump({"updated": "2026-11-25 10:00", "checked_at": 123,
                           "nodes": {}}, handle)
            with open(picks_path, "w", encoding="utf-8") as handle:
                json.dump({"lists": {"best": []}}, handle)
            with patch.object(threshold_watch, "SCORES", scores_path), \
                    patch.object(threshold_watch, "PICKS", picks_path):
                first = threshold_watch.observe()
                repeated = threshold_watch.observe()
                with open(picks_path, "w", encoding="utf-8") as handle:
                    json.dump({"lists": {"best": [{"fp": "different"}]}}, handle)
                changed = threshold_watch.observe()

        self.assertTrue(first.get("snapshot_id"))
        self.assertEqual(first["snapshot_id"], repeated["snapshot_id"])
        self.assertNotEqual(first["snapshot_id"], changed["snapshot_id"])

    def test_distinct_day_count_uses_timestamps_not_input_fingerprints(self):
        doc = {"rows": [
            {"ts": "2026-11-25 10:00", "snapshot_id": "input-A"},
            {"ts": "2026-11-26 10:00", "snapshot_id": "input-B"},
            {"ts": "2026-11-25 10:00", "snapshot_id": "input-C"},
        ]}

        self.assertEqual(threshold_watch.distinct_days(doc), 2)


if __name__ == "__main__":
    unittest.main()
