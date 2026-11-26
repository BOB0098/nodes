import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import history


class DailyP50Tests(unittest.TestCase):
    DAY = 20783  # 2026-11-26 (UTC+8 day number)

    def test_same_day_p50_uses_all_round_samples_not_last_round_or_round_medians(self):
        trend = {"days": []}
        history.trend_add(trend, 3, 3, [10, 20, 30], today=self.DAY)
        history.trend_add(trend, 3, 3, [100, 110, 120], today=self.DAY)

        # Combined sorted samples: 10, 20, 30, 100, 110, 120.
        # The project's lower-median convention yields 30.
        self.assertEqual(trend["days"][0]["p50"], 30)
        self.assertEqual(trend["days"][0]["rounds"], 2)

    def test_daily_frequency_table_survives_trend_json_round_trip(self):
        with tempfile.TemporaryDirectory(prefix="history-p50-") as tmp:
            trend_path = os.path.join(tmp, "trend.json")
            with patch.object(history, "TREND", trend_path):
                trend = {"days": []}
                history.trend_add(trend, 3, 3, [10, 20, 30], today=self.DAY)
                self.assertTrue(history.save_trend(trend, force=True, quiet=True))
                with open(trend_path, encoding="utf-8") as f:
                    loaded = json.load(f)
                history.trend_add(loaded, 3, 3, [100, 110, 120], today=self.DAY)

        self.assertEqual(loaded["days"][0]["p50"], 30)

    def test_empty_round_does_not_replace_the_daily_p50(self):
        trend = {"days": []}
        history.trend_add(trend, 2, 2, [20, 30], today=self.DAY)
        history.trend_add(trend, 0, 0, [], today=self.DAY)

        self.assertEqual(trend["days"][0]["p50"], 20)

    def test_day_rollover_keeps_final_p50_and_discards_old_day_samples(self):
        trend = {"days": []}
        history.trend_add(trend, 3, 3, [10, 20, 30], today=self.DAY)
        history.trend_add(trend, 1, 1, [50], today=self.DAY + 1)

        old_day, new_day = trend["days"]
        self.assertEqual(old_day["p50"], 20)
        self.assertNotIn("ms_hist", old_day)
        self.assertEqual(new_day["p50"], 50)

    def test_legacy_p50_without_raw_samples_is_preserved(self):
        trend = {"days": [{
            "d": "2026-11-26", "rounds": 1, "tested": 3, "ok": 3,
            "new": 0, "gone": 0, "p50": 110, "by_proto": {},
        }]}
        history.trend_add(trend, 3, 3, [10, 20, 30], today=self.DAY)
        history.trend_add(trend, 3, 3, [40, 50, 60], today=self.DAY)

        # Old p50-only rows cannot be exactly merged; preserve rather than invent
        # a synthetic distribution or rewrite the historical value.
        self.assertEqual(trend["days"][0]["p50"], 110)

    def test_daily_histogram_stays_within_the_365_day_trend_window(self):
        trend = {"days": []}
        for offset in range(history.TREND_DAYS + 1):
            history.trend_add(trend, 1, 1, [offset + 1], today=self.DAY + offset)

        self.assertEqual(len(trend["days"]), history.TREND_DAYS)
        self.assertEqual(trend["days"][0]["p50"], 2)
        self.assertEqual(trend["days"][-1]["p50"], history.TREND_DAYS + 1)


if __name__ == "__main__":
    unittest.main()
