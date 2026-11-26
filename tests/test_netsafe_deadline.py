#!/usr/bin/env python3
"""P24 C4 回归：一次请求与所有重定向共享同一个绝对时限。"""
import http.server
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
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


class NetsafePublicAddressTest(unittest.TestCase):
    def test_rejects_cgnat_ip_literal(self):
        with self.assertRaises(netsafe.Blocked):
            netsafe.check_url("http://100.64.0.1/feed")

    def test_rejects_cgnat_dns_answer(self):
        with patch.object(
            netsafe.socket, "getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                           ("100.64.0.1", 80))],
        ):
            with self.assertRaises(netsafe.Blocked):
                netsafe.check_url("http://feed.example/feed")


class NetsafePinnedTransportTest(unittest.TestCase):
    def test_explicit_zero_port_is_rejected_instead_of_defaulting(self):
        with patch.object(
            netsafe.socket, "getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                           ("93.184.216.34", 80))],
        ) as resolver:
            with self.assertRaises(netsafe.Blocked):
                netsafe.check_url("http://feed.example:0/feed")

        resolver.assert_not_called()

    def test_http_retries_only_the_validated_ips_and_keeps_original_host(self):
        hosts = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hosts.append(self.headers.get("Host"))
                body = b"pinned http"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 2)
        port = server.server_address[1]
        netsafe.ALLOW_PORTS.add(port)
        self.addCleanup(netsafe.ALLOW_PORTS.discard, port)
        checked_ips = ("93.184.216.34", "1.1.1.1")
        targets = []

        def dns(_host, requested_port, **_kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, requested_port))
                    for ip in checked_ips]

        def route_for_test(address, timeout=None, source_address=None, **_kwargs):
            targets.append(address)
            if address[0] == checked_ips[0]:
                raise OSError("simulated first validated address failure")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(("127.0.0.1", port))
            return sock

        with (
            patch.object(netsafe.socket, "getaddrinfo", side_effect=dns),
            patch.object(netsafe.socket, "create_connection", side_effect=route_for_test),
            patch.object(urllib.request, "getproxies", return_value={}),
            patch.object(netsafe, "_OPENER", None),
        ):
            body = netsafe.open_url(
                f"http://feed.example:{port}/feed", timeout=2, total_timeout=5
            )

        self.assertEqual(body, b"pinned http")
        self.assertEqual(targets, [(checked_ips[0], port), (checked_ips[1], port)])
        self.assertEqual(hosts, [f"feed.example:{port}"])

    def test_multi_ip_retries_share_remaining_deadline(self):
        clock = FakeClock()

        class FakeSocket:
            def close(self):
                pass

        targets = []
        conn = netsafe._PinnedHTTPConnection(
            "feed.example", timeout=10, pinned_ips=("93.184.216.34", "1.1.1.1"),
            deadline=1.0, total_timeout=1.0)

        def connect(address, timeout, _source_address=None):
            targets.append((address[0], timeout))
            if len(targets) == 1:
                clock.advance(0.65)
                raise OSError("simulated first address failure")
            return FakeSocket()

        with (
            patch.object(netsafe.time, "monotonic", clock.monotonic),
            patch.object(netsafe.socket, "create_connection", side_effect=connect),
        ):
            conn.connect()

        self.assertEqual([item[0] for item in targets], ["93.184.216.34", "1.1.1.1"])
        self.assertAlmostEqual(targets[0][1], 1.0)
        self.assertAlmostEqual(targets[1][1], 0.35)

    def test_https_pins_ips_but_keeps_sni_host_and_certificate_verification(self):
        hosts, sni = [], []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hosts.append(self.headers.get("Host"))
                body = b"pinned tls"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        with tempfile.TemporaryDirectory(prefix="netsafe-tls-") as td:
            cert, key = f"{td}/cert.pem", f"{td}/key.pem"
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                 "-keyout", key, "-out", cert, "-days", "1", "-subj",
                 "/CN=feed.example", "-addext", "subjectAltName=DNS:feed.example",
                 "-addext", "basicConstraints=critical,CA:TRUE"],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(cert, key)
            server_context.set_servername_callback(
                lambda _sock, name, _context: sni.append(name)
            )
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.socket = server_context.wrap_socket(server.socket, server_side=True)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            self.addCleanup(worker.join, 2)
            port = server.server_address[1]
            netsafe.ALLOW_PORTS.add(port)
            self.addCleanup(netsafe.ALLOW_PORTS.discard, port)
            checked_ips = ("93.184.216.34", "1.1.1.1")
            targets = []

            def dns(_host, requested_port, **_kwargs):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, requested_port))
                        for ip in checked_ips]

            def route_for_test(address, timeout=None, source_address=None, **_kwargs):
                targets.append(address)
                if address[0] == checked_ips[0]:
                    raise OSError("simulated first validated address failure")
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                sock.connect(("127.0.0.1", port))
                return sock

            client_context = ssl.create_default_context(cafile=cert)
            with (
                patch.object(netsafe.socket, "getaddrinfo", side_effect=dns),
                patch.object(netsafe.socket, "create_connection", side_effect=route_for_test),
                patch.object(urllib.request, "getproxies", return_value={}),
                patch.object(ssl, "create_default_context", return_value=client_context),
                patch.object(netsafe, "_OPENER", None),
            ):
                body = netsafe.open_url(
                    f"https://feed.example:{port}/feed", timeout=2, total_timeout=5
                )

            self.assertTrue(client_context.check_hostname)
            self.assertEqual(client_context.verify_mode, ssl.CERT_REQUIRED)
            self.assertEqual(body, b"pinned tls")
            self.assertEqual(targets, [(checked_ips[0], port), (checked_ips[1], port)])
            self.assertEqual(hosts, [f"feed.example:{port}"])
            self.assertEqual(sni, ["feed.example"])

    def test_https_retries_next_validated_ip_after_tls_handshake_failure(self):
        clock = FakeClock()

        class FakeSocket:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        first_raw, second_raw, wrapped = FakeSocket(), FakeSocket(), FakeSocket()
        checked_ips = ("93.184.216.34", "1.1.1.1")
        targets = []

        class FakeTLSContext:
            def wrap_socket(self, raw, server_hostname):
                self.server_hostname = server_hostname
                if raw is first_raw:
                    clock.advance(0.6)
                    raise ssl.SSLError("simulated TLS handshake failure")
                return wrapped

        conn = netsafe._PinnedHTTPSConnection(
            "feed.example", timeout=5, context=FakeTLSContext(),
            pinned_ips=checked_ips, deadline=1.0, total_timeout=1.0)

        def connected(address, timeout, _source_address=None):
            targets.append((address[0], timeout))
            return first_raw if len(targets) == 1 else second_raw

        with (
            patch.object(netsafe.time, "monotonic", clock.monotonic),
            patch.object(netsafe.socket, "create_connection", side_effect=connected),
        ):
            conn.connect()

        self.assertEqual([ip for ip, _ in targets], list(checked_ips))
        self.assertAlmostEqual(targets[0][1], 1.0)
        self.assertAlmostEqual(targets[1][1], 0.4)
        self.assertTrue(first_raw.closed)
        self.assertFalse(second_raw.closed)
        self.assertIs(conn.sock, wrapped)
        self.assertEqual(conn._context.server_hostname, "feed.example")

    def test_redirect_carries_each_hops_validated_addresses(self):
        clock = FakeClock()
        handler = netsafe._SafeRedirect()
        first_req = urllib.request.Request("https://first.example/start")
        first_req.timeout = 15
        first_req._netsafe_deadline = 100
        first_req._netsafe_total_timeout = 90
        first_req._netsafe_connect_timeout = 15

        with (
            patch.object(netsafe.time, "monotonic", clock.monotonic),
            patch.object(netsafe, "check_url", side_effect=[("93.184.216.34",),
                                                               ("1.1.1.1",)]) as check,
        ):
            second_req = handler.redirect_request(
                first_req, None, 302, "Found", {}, "https://second.example/next"
            )
            third_req = handler.redirect_request(
                second_req, None, 302, "Found", {}, "https://third.example/final"
            )

        self.assertEqual(check.call_count, 2)
        self.assertEqual(getattr(second_req, "_netsafe_ips", None), ("93.184.216.34",))
        self.assertEqual(getattr(third_req, "_netsafe_ips", None), ("1.1.1.1",))

    def test_real_opener_follows_redirect_using_each_hops_pinned_ip(self):
        observed_hosts = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                observed_hosts.append(self.headers.get("Host"))
                if self.headers.get("Host", "").startswith("origin.example:"):
                    self.send_response(302)
                    self.send_header("Location", f"http://target.example:{port}/final")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = b"redirect target"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join, 2)
        netsafe.ALLOW_PORTS.add(port)
        self.addCleanup(netsafe.ALLOW_PORTS.discard, port)

        pinned = {"origin.example": "93.184.216.34", "target.example": "1.1.1.1"}
        targets = []

        def dns(host, requested_port, **_kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                     (pinned[host], requested_port))]

        def route_for_test(address, timeout=None, source_address=None, **_kwargs):
            targets.append(address)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(("127.0.0.1", port))
            return sock

        with (
            patch.object(netsafe.socket, "getaddrinfo", side_effect=dns),
            patch.object(netsafe.socket, "create_connection", side_effect=route_for_test),
            patch.object(urllib.request, "getproxies", return_value={}),
            patch.object(netsafe, "_OPENER", None),
        ):
            body = netsafe.open_url(
                f"http://origin.example:{port}/start", timeout=2, total_timeout=5
            )

        self.assertEqual(body, b"redirect target")
        self.assertEqual(observed_hosts, [f"origin.example:{port}", f"target.example:{port}"])
        self.assertEqual(targets, [(pinned["origin.example"], port),
                                  (pinned["target.example"], port)])

    def test_response_and_socket_close_when_body_read_raises(self):
        class TrackingSocket:
            def __init__(self):
                self.closed = False

            def settimeout(self, _timeout):
                pass

            def close(self):
                self.closed = True

        class ReadFailureResponse:
            headers = {}

            def __init__(self):
                self.sock = TrackingSocket()
                self.closed = False

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                self.close()

            def read1(self, _size=65536):
                raise OSError("simulated body read failure")

            def close(self):
                self.closed = True
                self.sock.close()

        response = ReadFailureResponse()
        fake_opener = FakeOpener(response)
        with (
            patch.object(urllib.request, "getproxies", return_value={}),
            patch.object(netsafe, "check_url", return_value=("93.184.216.34",)),
            patch.object(netsafe, "opener", return_value=fake_opener),
        ):
            with self.assertRaisesRegex(OSError, "simulated body read failure"):
                netsafe.open_url("http://feed.example/feed", timeout=2, total_timeout=5)

        self.assertTrue(response.closed)
        self.assertTrue(response.sock.closed)

    def test_unpinnable_proxy_fails_closed_before_opening_request(self):
        fake_opener = FakeOpener(FakeResponse(reads=[(0.0, b"should not read")]))
        with (
            patch.object(urllib.request, "getproxies",
                         return_value={"http": "http://proxy.example:3128"}),
            patch.object(urllib.request, "proxy_bypass", return_value=False),
            patch.object(netsafe, "check_url", return_value=("93.184.216.34",)),
            patch.object(netsafe, "opener", return_value=fake_opener),
        ):
            with self.assertRaises(netsafe.Blocked):
                netsafe.open_url("http://feed.example/feed", timeout=2, total_timeout=5)
        self.assertEqual(fake_opener.calls, [])

    def test_all_proxy_is_not_ignored_by_route_gate(self):
        with (
            patch.object(urllib.request, "getproxies",
                         return_value={"all": "socks5://proxy.example:1080"}),
            patch.object(urllib.request, "proxy_bypass", return_value=False),
        ):
            with self.assertRaises(netsafe.Blocked):
                netsafe._require_direct_route("https://feed.example/feed")


class NetsafeSocketCleanupTest(unittest.TestCase):
    def test_http_closes_socket_if_deadline_expires_after_connect(self):
        clock = FakeClock()

        class FakeSocket:
            closed = False

            def close(self):
                self.closed = True

        sock = FakeSocket()

        def connected(*_args, **_kwargs):
            clock.advance(1.1)
            return sock

        conn = netsafe._PinnedHTTPConnection(
            "feed.example", timeout=2, pinned_ips=("93.184.216.34",),
            deadline=1.0, total_timeout=1.0)
        with (
            patch.object(netsafe.time, "monotonic", clock.monotonic),
            patch.object(netsafe.socket, "create_connection", side_effect=connected),
        ):
            with self.assertRaises(netsafe.TooSlow):
                conn.connect()

        self.assertTrue(sock.closed, "超时后的已建立 TCP socket 必须关闭")

    def test_https_closes_wrapped_socket_if_deadline_expires_after_tls_wrap(self):
        clock = FakeClock()

        class FakeSocket:
            closed = False

            def close(self):
                self.closed = True

        raw, wrapped = FakeSocket(), FakeSocket()

        class FakeTLSContext:
            def wrap_socket(self, _raw, server_hostname):
                self.server_hostname = server_hostname
                clock.advance(1.1)
                return wrapped

        conn = netsafe._PinnedHTTPSConnection(
            "feed.example", timeout=2, context=FakeTLSContext(),
            pinned_ips=("93.184.216.34",), deadline=1.0, total_timeout=1.0)
        with (
            patch.object(netsafe.time, "monotonic", clock.monotonic),
            patch.object(netsafe.socket, "create_connection", return_value=raw),
        ):
            with self.assertRaises(netsafe.TooSlow):
                conn.connect()

        self.assertEqual(conn._context.server_hostname, "feed.example")
        self.assertTrue(raw.closed)
        self.assertTrue(wrapped.closed, "超时后的已包装 TLS socket 必须关闭")


if __name__ == "__main__":
    unittest.main(verbosity=2)
