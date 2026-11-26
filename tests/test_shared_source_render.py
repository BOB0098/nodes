#!/usr/bin/env python3
"""Offline regression for page cards backed by a shared source URL."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import collector  # noqa: E402


class SharedSourceRenderTests(unittest.TestCase):
    def test_each_card_uses_its_source_id_for_real_and_lifecycle_stats(self):
        url = "https://feed.example.invalid/sub"
        sid_a, sid_b = "source-alpha", "source-beta"
        groups = [{
            "title": "Shared", "kern": "kernel", "clients": "client",
            "items": [
                {"tag": "Alpha", "note": "A", "ok": [], "no": [],
                 "freq": "daily", "url": url, "sid": sid_a},
                {"tag": "Beta", "note": "B", "ok": [], "no": [],
                 "freq": "daily", "url": url, "sid": sid_b},
            ],
        }]
        real = {
            sid_a: {"ok": 2, "tested": 3},
            sid_b: {"ok": 7, "tested": 9},
        }
        fresh = {
            sid_a: {"new7": 11, "median_age": 4},
            sid_b: {"new7": 22, "median_age": 8},
        }
        grades = {
            sid_a: {"grade": "A", "score": 81, "state": "ok", "priority_eff": 2},
            sid_b: {"grade": "C", "score": 61, "state": "watch", "priority_eff": 5},
        }
        node = {"name": "Shared node", "proto": "trojan", "region": "日本",
                "addr": "example.invalid:443", "src": "Alpha", "uri": "trojan://secret",
                "src_ids": [sid_a, sid_b], "src_names": ["Alpha", "Beta"]}

        with patch.object(collector, "GROUPS", groups), \
                patch.object(collector, "_load_scores", return_value={}), \
                patch.object(collector, "_load_sub_counts", return_value={}), \
                patch.object(collector, "_load_quarantine", return_value={}), \
                patch.object(collector, "_load_real", return_value={}):
            data = collector.assemble(
                nodes=[node], raw_total=0, errors=[], counts={url: 1}, probes={},
                delta=None, health={}, real=real, fresh=fresh, grades=grades, life_span=10)

        cards = {card["sid"]: card for card in data["groups"][0]["items"]}
        self.assertEqual((cards[sid_a]["rok"], cards[sid_a]["rtest"],
                          cards[sid_a]["new7"], cards[sid_a]["med_age"]), (2, 3, 11, 4))
        self.assertEqual((cards[sid_b]["rok"], cards[sid_b]["rtest"],
                          cards[sid_b]["new7"], cards[sid_b]["med_age"]), (7, 9, 22, 8))
        self.assertEqual((cards[sid_a]["grade"], cards[sid_a]["src_state"]), ("A", "ok"))
        self.assertEqual((cards[sid_b]["grade"], cards[sid_b]["src_state"]), ("C", "watch"))
        self.assertEqual(data["nodes"][0]["src_ids"], [sid_a, sid_b])
        self.assertEqual(data["nodes"][0]["src_names"], ["Alpha", "Beta"])
        self.assertNotIn("uri", data["nodes"][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
