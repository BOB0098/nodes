#!/usr/bin/env python3
"""锁定来源评分的关键数值契约，不改动评分策略。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import score  # noqa: E402


class SourceScoreContractTests(unittest.TestCase):
    @staticmethod
    def maximal_row(**overrides):
        row = {
            "rtest": 1200,
            "rok": 840,
            "alive_pct": 70,
            "unique": 1500,
            "parsed": 3000,
            "fails_streak": 0,
            "source_age_h": 2,
            "license": "MIT",
            "type": "original",
        }
        row.update(overrides)
        return row

    @staticmethod
    def dimension_values(row, history=None):
        _, dims = score.score_row(row, history)
        return {name: (value, maximum) for name, value, maximum, _ in dims}

    def test_documented_dimension_weights_are_the_runtime_maxima(self):
        history = [{"real_pct": 70.0}, {"real_pct": 70.0}]
        total, raw_dims = score.score_row(self.maximal_row(), history)
        dims = {name: (value, maximum) for name, value, maximum, _ in raw_dims}

        self.assertEqual(dims["真实可用率"], (32.0, 32))
        self.assertEqual(dims["真实可用条数"], (10.0, 10))
        self.assertEqual(dims["样本置信"], (4.0, 4))
        self.assertEqual(dims["独立新增"], (10.0, 10))
        self.assertEqual(dims["独立率"], (6.0, 6))
        self.assertEqual(dims["新鲜度"], (10.0, 10))
        self.assertEqual(dims["稳定性"], (14.0, 14))
        self.assertEqual(dims["TCP 可连率"], (8.0, 8))
        self.assertEqual(dims["合规/类型"], (6.0, 6))
        self.assertEqual(total, 100.0)

    def test_missing_real_samples_raise_tcp_dimension_ceiling_to_27_2(self):
        row = self.maximal_row(rtest=0, rok=0, alive_pct=70)
        history = [{"real_pct": 0.0}, {"real_pct": 0.0}]

        total, dims = score.score_row(row, history)
        tcp = next(d for d in dims if d[0] == "TCP 可连率")

        self.assertEqual(tcp[1:3], (27.2, 27.2))
        self.assertEqual(tcp[3], "可连 70%")
        self.assertEqual(total, 73.2)
        self.assertEqual(score.cap_grade(score.grade_of(total), 0), "B")

    def test_missing_tcp_measurement_gets_half_of_the_dynamic_ceiling(self):
        row = self.maximal_row(rtest=0, rok=0, alive_pct=None)
        history = [{"real_pct": 0.0}, {"real_pct": 0.0}]

        total, dims = score.score_row(row, history)
        tcp = next(d for d in dims if d[0] == "TCP 可连率")

        self.assertEqual(tcp[1:3], (13.6, 27.2))
        self.assertEqual(tcp[3], "未抽样，按中位计")
        self.assertEqual(total, 59.6)

    def test_tcp_ceiling_returns_to_eight_when_real_samples_exist(self):
        row = self.maximal_row(rtest=1, rok=1, alive_pct=70)
        dims = self.dimension_values(row, [{"real_pct": 100.0}, {"real_pct": 100.0}])

        self.assertEqual(dims["TCP 可连率"], (8.0, 8))

    def test_grade_cutoffs_remain_unchanged(self):
        cases = (
            (90, "S"), (89.9, "A"), (80, "A"), (79.9, "B"),
            (65, "B"), (64.9, "C"), (50, "C"), (49.9, "D"),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(score.grade_of(value), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
