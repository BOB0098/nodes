#!/usr/bin/env python3
"""P24 C4 回归：一次请求与所有重定向共享同一个绝对时限。"""
import socket
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import netsafe  # noqa: E402
import discover  # noqa: E402
import collector  # noqa: E402
import governance  # noqa: E402
import screen  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeResponse:
    def __init__(self, clock=None, reads=(), headers=None):
        self.headers = headers or {}
        self.status = 200
        self.clock = clock
        self.reads = list(reads)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, _size=65536):
        if not self.reads:
            return b""
        elapsed, chunk = self.reads.pop(0)
        if self.clock is not None:
            self.clock.advance(elapsed)
        return chunk


class FakeOpener:
    def __init__(self, response, clock=None, connect_elapsed=0.0):
        self.response = response
        self.clock = clock
        self.connect_elapsed = connect_elapsed
        self.calls = []

    def open(self, req, timeout):
        self.calls.append((req, timeout))
        if self.clock is not None:
            self.clock.advance(self.connect_elapsed)
        return self.response


class FakeRedirectBody:
    def __init__(self):
        self.read_calls = 0
        self.closed = False

    def read(self, _size=-1):
        self.read_calls += 1
        return b"redirect body"

    def close(self):
        self.closed = True


class RecordingParent:
    def __init__(self):
        self.calls = []
        self.response = object()

    def open(self, req, timeout):
        self.calls.append((req, timeout))
        return self.response


class NetsafeDeadlineTest(unittest.TestCase):
    def test_dns_time_reduces_initial_connection_timeout(self):
        clock = FakeClock()
        response = FakeResponse(clock, [(0.0, b"ok"), (0.0, b"")])
        fake_opener = FakeOpener(response, clock)

        def slow_dns(_host, port, **_kwargs):
            clock.advance(0.75)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

        with (
            patch.object(netsafe.time, "monotonic", clock.monotonic),
            patch.object(netsafe.socket, "getaddrinfo", side_effect=slow_dns),
            patch.object(netsafe, "opener", return_value=fake_opener),
        ):
            body = netsafe.open_url(
                "https://feed.example/sub", timeout=15, total_timeout=1.0
            )

        self.assertEqual(body, b"ok")
        self.assertAlmostEqual(fake_opener.calls[0][1], 0.25)

    def test_expired_dns_result_is_rejected_before_connection(self):
        clock = FakeClock()
        response = FakeResponse()
        fake_opener = FakeOpener(response)

        def slow_dns(_host, port, **_kwargs):
            clock.advance(1.1)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

        with (
            patch.object(netsafe.time, "monotonic", clock.monotonic),
            patch.object(netsafe.socket, "getaddrinfo", side_effect=slow_dns),
            patch.object(netsafe, "opener", return_value=fake_opener),
        ):
            with self.assertRaises(netsafe.TooSlow):
                netsafe.open_url(
                    "https://feed.example/sub", timeout=15, total_timeout=1.0
                )

        self.assertEqual(fake_opener.calls, [])

    def test_connection_and_body_share_one_deadline(self):
        clock = FakeClock()
        response = FakeResponse(clock, [(0.5, b"chunk"), (0.0, b"")])
        fake_opener = FakeOpener(response, clock, connect_elapsed=0.6)

        with (
            patch.object(netsafe.time, "monotonic", clock.monotonic),
            patch.object(netsafe, "opener", return_value=fake_opener),
        ):
            with self.assertRaises(netsafe.TooSlow):
                netsafe.open_url(
                    "https://93.184.216.34/sub", timeout=15, total_timeout=1.0
                )

    def test_redirect_response_is_closed_without_unbounded_body_drain(self):
        body = FakeRedirectBody()
        parent = RecordingParent()
        handler = netsafe._SafeRedirect()
        handler.parent = parent
        req = urllib.request.Request("https://origin.example/start")
        req.timeout = 15

        with patch.object(netsafe, "check_url", return_value=None):
            result = handler.http_error_302(
                req, body, 302, "Found", {"location": "/next"}
            )

        self.assertIs(result, parent.response)
        self.assertTrue(body.closed)
        self.assertEqual(body.read_calls, 0)
        self.assertEqual(len(parent.calls), 1)

    def test_redirect_hops_share_deadline_and_remaining_timeout(self):
        clock = FakeClock()
        parent = RecordingParent()
        handler = netsafe._SafeRedirect()
        handler.parent = parent
        req = urllib.request.Request("https://origin.example/start")
        req.timeout = 15
        req._netsafe_deadline = 1.0
        req._netsafe_connect_timeout = 15.0

        def delayed_check(_url, *_args, **_kwargs):
            clock.advance(0.6)

        with (
            patch.object(netsafe.time, "monotonic", clock.monotonic),
            patch.object(netsafe, "check_url", side_effect=delayed_check),
        ):
            first = handler.http_error_302(
                req, FakeRedirectBody(), 302, "Found", {"location": "/next"}
            )
            self.assertIsNotNone(first)
            self.assertAlmostEqual(parent.calls[0][1], 0.4)
            next_req = parent.calls[0][0]
            with self.assertRaises(netsafe.TooSlow):
                handler.http_error_302(
                    next_req, FakeRedirectBody(), 302, "Found", {"location": "/last"}
                )

        self.assertEqual(len(parent.calls), 1)

    def test_discover_api_connection_time_is_inside_total_budget(self):
        clock = FakeClock()
        response = FakeResponse(clock, [(0.0, b'{"ok": true}'), (0.0, b"")])
        fake_opener = FakeOpener(
            response, clock, connect_elapsed=netsafe.TOTAL_TIMEOUT + 1.0
        )

        with (
            patch.object(netsafe.time, "monotonic", clock.monotonic),
            patch.object(netsafe, "check_url", return_value=None),
            patch.object(netsafe, "opener", return_value=fake_opener),
            patch.object(discover.time, "sleep", return_value=None),
        ):
            result = discover.api("/repos/owner/project")

        self.assertEqual(result, (None, {}))
        self.assertEqual(len(fake_opener.calls), 2)

    def test_discover_api_keeps_response_headers_with_budgeted_fetch(self):
        clock = FakeClock()
        response = FakeResponse(
            clock, [(0.0, b'{"ok": true}'), (0.0, b"")],
            headers={"X-RateLimit-Remaining": "9"})
        fake_opener = FakeOpener(response, clock)

        with (
            patch.object(netsafe.time, "monotonic", clock.monotonic),
            patch.object(netsafe, "check_url", return_value=None),
            patch.object(netsafe, "opener", return_value=fake_opener),
            patch.object(discover.time, "sleep", return_value=None),
        ):
            result = discover.api("/repos/owner/project")

        self.assertEqual(
            result, ({"ok": True}, {"X-RateLimit-Remaining": "9"})
        )

    def test_collector_http_probe_counts_connection_time(self):
        clock = FakeClock()
        response = FakeResponse(
            clock, [(0.0, b"ok"), (0.0, b"")], headers={"Content-Type": "text/plain"})
        fake_opener = FakeOpener(
            response, clock, connect_elapsed=netsafe.TOTAL_TIMEOUT + 1.0
        )

        with (
            patch.object(netsafe.time, "monotonic", clock.monotonic),
            patch.object(netsafe, "check_url", return_value=None),
            patch.object(netsafe, "opener", return_value=fake_opener),
        ):
            result = collector.http_probe("https://example.com/feed")

        self.assertEqual(result["code"], 0)
        self.assertEqual(result["err"], "TooSlow")

    def test_collector_head_metadata_counts_connection_time(self):
        clock = FakeClock()
        response = FakeResponse(
            clock, headers={"Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT"})
        fake_opener = FakeOpener(
            response, clock, connect_elapsed=netsafe.TOTAL_TIMEOUT + 1.0
        )

        with (
            patch.object(netsafe.time, "monotonic", clock.monotonic),
            patch.object(netsafe, "check_url", return_value=None),
            patch.object(netsafe, "opener", return_value=fake_opener),
        ):
            result = collector.src_meta("https://example.com/feed")

        self.assertIsNone(result)
        self.assertEqual(fake_opener.calls[0][0].get_method(), "HEAD")

    def test_discover_content_sniff_counts_connection_time(self):
        clock = FakeClock()
        subscription = b"".join([b"vmess://node\n"] * 5)
        response = FakeResponse(clock, [(0.0, subscription), (0.0, b"")])
        fake_opener = FakeOpener(
            response, clock, connect_elapsed=netsafe.TOTAL_TIMEOUT + 1.0
        )

        with (
            patch.object(netsafe.time, "monotonic", clock.monotonic),
            patch.object(netsafe, "check_url", return_value=None),
            patch.object(netsafe, "opener", return_value=fake_opener),
        ):
            result = discover.looks_like_subscription("https://example.com/feed")

        self.assertIsNone(result)

    def test_governance_open_source_counts_connection_time(self):
        clock = FakeClock()
        response = FakeResponse(
            clock, [(0.0, b"vmess://node\n"), (0.0, b"")],
            headers={"Content-Type": "text/plain"})
        fake_opener = FakeOpener(
            response, clock, connect_elapsed=netsafe.TOTAL_TIMEOUT + 1.0
        )

        with (
            patch.object(netsafe.time, "monotonic", clock.monotonic),
            patch.object(netsafe, "check_url", return_value=None),
            patch.object(netsafe, "opener", return_value=fake_opener),
        ):
            with self.assertRaises(netsafe.TooSlow):
                governance.open_source("https://example.com/feed")

    def test_screen_fetch_counts_connection_time(self):
        clock = FakeClock()
        response = FakeResponse(
            clock, [(0.0, b"vmess://node\n"), (0.0, b"")],
            headers={"Content-Type": "text/plain"})
        fake_opener = FakeOpener(
            response, clock, connect_elapsed=netsafe.TOTAL_TIMEOUT + 1.0
        )

        with (
            patch.object(netsafe.time, "monotonic", clock.monotonic),
            patch.object(netsafe, "check_url", return_value=None),
            patch.object(netsafe, "opener", return_value=fake_opener),
        ):
            result = screen.fetch("https://example.com/feed")

        self.assertEqual(result["code"], 0)
        self.assertEqual(result["err"], "TooSlow")


if __name__ == "__main__":
    unittest.main(verbosity=2)
