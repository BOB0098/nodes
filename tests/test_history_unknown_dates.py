import json
import os
import sys
import tempfile
import unittest
import warnings
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import history
import lifecycle
import picks


class UnknownHistoryDatesTests(unittest.TestCase):
    TODAY = 20783
    FP = "a" * 32
    KEY = FP[:16]

    def test_migration_keeps_dates_unknown_and_preserves_cumulative_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy_path = os.path.join(tmp, "legacy.json")
            with open(legacy_path, "w", encoding="utf-8") as f:
                json.dump({"nodes": {self.FP: {
                    "ok_streak": 2, "fail_streak": 1,
                    "last_ok": True, "checked": 9,
                }}}, f)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                with patch.object(history, "LEGACY", legacy_path), \
                        patch.object(history, "day_of", return_value=self.TODAY):
                    doc, migrated = history.migrate_legacy({"schema": 2, "nodes": {}})

        rec = doc["nodes"][self.KEY]
        self.assertFalse(any(issubclass(w.category, ResourceWarning) for w in caught))
        self.assertEqual(migrated, 1)
        self.assertIsNone(rec["first"])
        self.assertIsNone(rec["last"])
        self.assertIsNone(rec["last_ok"])
        self.assertEqual((rec["tries"], rec["hits"]), (9, 2))
        self.assertTrue(rec["migrated"])
        self.assertFalse(rec["observed_since_migration"])
        compacted = history._compact(rec)
        self.assertIsNone(compacted["first"])
        self.assertIsNone(compacted["last"])
        self.assertFalse(compacted["observed_since_migration"])

    def test_unobserved_migrated_record_with_legacy_synthetic_date_is_not_recent(self):
        hist = {"nodes": {self.KEY: {
            "first": self.TODAY, "last": self.TODAY,
            "last_ok": self.TODAY, "tries": 4, "hits": 4,
            "migrated": True,
        }}}
        self.assertEqual(picks._cands({"nodes": {}}, hist, self.TODAY), {})
        failed_check = {"nodes": {self.FP: {"ok": False}}}
        self.assertEqual(picks._cands(failed_check, hist, self.TODAY), {})

    def test_unknown_migration_dates_do_not_expire_or_count_as_gone(self):
        doc = {"nodes": {self.KEY: {
            "first": None, "last": None, "last_ok": None,
            "tries": 4, "hits": 2, "migrated": True,
            "observed_since_migration": False,
        }}}
        stats = history.observe(doc, {}, today=self.TODAY)
        self.assertEqual(stats["gone"], 0)
        self.assertEqual(history.prune(doc, today=self.TODAY), 0)
        self.assertIn(self.KEY, doc["nodes"])

    def test_first_real_observation_replaces_unknown_dates_without_erasing_counts(self):
        doc = {"nodes": {self.KEY: {
            # 模拟已落盘的旧迁移记录：日期看似今天，但来源是迁移占位。
            "first": self.TODAY, "last": self.TODAY,
            "last_ok": self.TODAY, "tries": 4, "hits": 4,
            "ok_run": 4, "fail_run": 0, "migrated": True,
        }}}
        observed_day = self.TODAY + 20
        history.observe(doc, {self.FP: {"ok": False}}, today=observed_day, slot=0)
        rec = doc["nodes"][self.KEY]
        self.assertEqual((rec["first"], rec["last"]), (observed_day, observed_day))
        self.assertIsNone(rec["last_ok"])
        self.assertEqual((rec["tries"], rec["hits"]), (5, 4))
        self.assertTrue(rec["migrated"])
        self.assertTrue(rec["observed_since_migration"])
        # 失败的真实观测不能让占位的旧成功日期重新进入近期候选。
        self.assertEqual(picks._cands({"nodes": {}}, doc, observed_day), {})

    def test_real_recent_observation_is_eligible_and_migration_can_age_into_it(self):
        hist = {"nodes": {self.KEY: {
            "first": self.TODAY - 10, "last": self.TODAY,
            "last_ok": self.TODAY - picks.HIST_KEEP_DAYS,
            "tries": 4, "hits": 4, "migrated": True,
            "observed_since_migration": True,
        }}}
        cands = picks._cands({"nodes": {}}, hist, self.TODAY)
        self.assertIn(self.KEY, cands)
        self.assertIsNone(cands[self.KEY]["rec"])

    def test_successful_real_observation_starts_known_dates_without_losing_history(self):
        doc = {"nodes": {self.KEY: {
            "first": None, "last": None, "last_ok": None,
            "tries": 4, "hits": 4, "migrated": True,
            "observed_since_migration": False,
        }}}
        observed_day = self.TODAY + 1
        history.observe(doc, {self.FP: {"ok": True}}, today=observed_day, slot=1)
        rec = doc["nodes"][self.KEY]
        self.assertEqual((rec["first"], rec["last"], rec["last_ok"]),
                         (observed_day, observed_day, observed_day))
        self.assertEqual((rec["tries"], rec["hits"]), (5, 5))
        cands = picks._cands({"nodes": {}}, doc, observed_day)
        self.assertIn(self.KEY, cands)
        persisted = {"nodes": {self.KEY: history._compact(rec)}}
        self.assertIn(self.KEY, picks._cands({"nodes": {}}, persisted, observed_day))

    def test_lifecycle_collection_history_remains_independent(self):
        hist = {"nodes": {self.KEY: {
            "first": None, "last": None, "last_ok": None,
            "tries": 4, "hits": 2, "migrated": True,
            "observed_since_migration": False,
        }}}
        lifecycle_doc, stat = lifecycle.update([{"fp": self.FP}], doc={"nodes": {}},
                                               today=self.TODAY)
        self.assertEqual(lifecycle_doc["nodes"][self.KEY], [self.TODAY, self.TODAY])
        self.assertEqual(stat["seen"], 1)
        self.assertIsNone(hist["nodes"][self.KEY]["last"])


if __name__ == "__main__":
    unittest.main()
