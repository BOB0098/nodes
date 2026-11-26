#!/usr/bin/env python3
"""Offline regressions for avoiding duplicate probes on shared source URLs."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import collector  # noqa: E402


class SharedSourceProbeTests(unittest.TestCase):
    def test_measure_fetches_each_primary_url_once(self):
        url = "https://feed.example.invalid/sub"
        items = [{"url": url}, {"url": url}]
        calls = []

        def fake_fetch(urls, name, use_cache=False):
            calls.append((list(urls), name))
            return b"trojan://pass@example.com:443#SharedNode\n"

        with patch.object(collector, "fetch", side_effect=fake_fetch), \
                patch.object(collector, "probe_all", return_value={}):
            counts, _probes, fingerprints, addresses = collector.measure(items)

        self.assertEqual(len(calls), 1)
        self.assertEqual(counts[url], 1)
        self.assertIn(url, fingerprints)
        self.assertEqual(addresses[url], ["example.com:443"])

    def test_source_and_metadata_probes_run_once_per_primary_url(self):
        url = "https://feed.example.invalid/sub"
        items = [{"url": url}, {"url": url}]
        source_calls, meta_calls = [], []

        def fake_http(url_to_probe):
            source_calls.append(url_to_probe)
            return {"code": 200, "ms": 5}

        def fake_meta(url_to_probe):
            meta_calls.append(url_to_probe)
            return {"age_h": 3, "ts": 123}

        with patch.object(collector, "URLS_BY_URL", {url: [url]}), \
                patch.object(collector, "http_probe", side_effect=fake_http), \
                patch.object(collector, "src_meta", side_effect=fake_meta):
            source_status = collector.probe_sources(items)
            metadata = collector.probe_meta(items)

        self.assertEqual(source_calls, [url])
        self.assertEqual(meta_calls, [url])
        self.assertEqual(source_status[url]["code"], 200)
        self.assertEqual(metadata[url]["age_h"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
