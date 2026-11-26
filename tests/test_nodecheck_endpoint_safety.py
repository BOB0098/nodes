#!/usr/bin/env python3
"""P24 C1 regression: only proxy endpoints pinned to public IPs may launch mihomo."""
import socket
import sys
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import nodecheck  # noqa: E402


class _Process:
    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class NodecheckEndpointSafetyTest(unittest.TestCase):
    def make_node(self, host, sni=None):
        node = {
            "fp": "endpoint-fingerprint",
            "type": "vless",
            "server": host,
            "port": 443,
            "cred": "endpoint-uuid",
            "security": "tls",
            "network": "tcp",
        }
        if sni:
            node["sni"] = sni
        return node

    def run_batch_with_dns(self, host, dns_result, *, dns_side_effect=False, sni=None):
        configs = []
        node = self.make_node(host, sni)

        def spawn(arguments, **kwargs):
            config_path = arguments[arguments.index("-f") + 1]
            configs.append(Path(config_path).read_text(encoding="utf-8"))
            return _Process()

        resolver_kwargs = ({"side_effect": dns_result}
                           if dns_side_effect or isinstance(dns_result, BaseException)
                           else {"return_value": dns_result})
        with (
            patch.object(nodecheck, "popen_mihomo", side_effect=spawn) as popen,
            patch.object(nodecheck.socket, "getaddrinfo", **resolver_kwargs) as resolver,
            patch.object(nodecheck, "_port_free", return_value=False),
            patch.object(nodecheck.time, "sleep"),
            patch.object(nodecheck, "probe", return_value=(False, None, "http:403")),
        ):
            result = nodecheck.run_batch([node], 24000)
        return result, popen, resolver, configs, node

    def addr(self, family, host):
        sockaddr = (host, 443) if family == socket.AF_INET else (host, 443, 0, 0)
        return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)

    def assert_rejected_before_mihomo(self, host, dns_result):
        result, popen, _, _, _ = self.run_batch_with_dns(host, dns_result)
        popen.assert_not_called()
        record = result["endpoint-fingerprint"]
        self.assertFalse(record["ok"])
        self.assertTrue(record["err"].startswith("proxy:"), record)

    def test_loopback_literal_is_rejected_before_mihomo(self):
        self.assert_rejected_before_mihomo(
            "127.0.0.1", [self.addr(socket.AF_INET, "8.8.8.8")])

    def test_cloud_metadata_literal_is_rejected_before_mihomo(self):
        self.assert_rejected_before_mihomo(
            "169.254.169.254", [self.addr(socket.AF_INET, "8.8.8.8")])

    def test_ipv4_mapped_loopback_literal_is_rejected_before_mihomo(self):
        self.assert_rejected_before_mihomo(
            "::ffff:127.0.0.1", [self.addr(socket.AF_INET, "8.8.8.8")])

    def test_non_global_ipv6_literals_are_rejected_before_mihomo(self):
        for host in ("::", "::1", "fc00::1", "fe80::1", "ff02::1",
                     "::ffff:169.254.169.254"):
            with self.subTest(host=host):
                self.assert_rejected_before_mihomo(
                    host, [self.addr(socket.AF_INET, "8.8.8.8")])

    def test_domain_resolving_to_private_address_is_rejected_before_mihomo(self):
        self.assert_rejected_before_mihomo(
            "node.example", [self.addr(socket.AF_INET, "10.4.5.6")])

    def test_domain_resolving_to_ipv6_link_local_is_rejected_before_mihomo(self):
        self.assert_rejected_before_mihomo(
            "node.example", [self.addr(socket.AF_INET6, "fe80::1")])

    def test_domain_resolving_to_mapped_private_address_is_rejected_before_mihomo(self):
        self.assert_rejected_before_mihomo(
            "node.example", [self.addr(socket.AF_INET6, "::ffff:127.0.0.1")])

    def test_mixed_public_and_private_dns_answers_are_rejected(self):
        self.assert_rejected_before_mihomo(
            "node.example", [self.addr(socket.AF_INET, "8.8.8.8"),
                              self.addr(socket.AF_INET, "10.4.5.6")])

    def test_dns_failure_is_rejected_before_mihomo(self):
        self.assert_rejected_before_mihomo(
            "missing.example", socket.gaierror("name resolution failed"))

    def test_public_dns_answer_is_pinned_and_default_tls_sni_is_preserved(self):
        public_answer = [self.addr(socket.AF_INET, "8.8.8.8")]
        later_private_answer = [self.addr(socket.AF_INET, "127.0.0.1")]
        result, popen, resolver, configs, node = self.run_batch_with_dns(
            "edge.example", [public_answer, later_private_answer], dns_side_effect=True)
        popen.assert_called_once()
        resolver.assert_called_once_with("edge.example", 443, type=socket.SOCK_STREAM)
        self.assertEqual(len(configs), 1)
        self.assertIn('server: "8.8.8.8"', configs[0])
        self.assertNotIn('server: "edge.example"', configs[0])
        self.assertIn('servername: "edge.example"', configs[0])
        self.assertEqual(node["server"], "edge.example")
        self.assertEqual(result["endpoint-fingerprint"]["err"], "http:403")

    def test_explicit_sni_is_preserved_when_server_is_pinned(self):
        result, popen, resolver, configs, _ = self.run_batch_with_dns(
            "edge.example", [self.addr(socket.AF_INET, "8.8.8.8")],
            sni="tls.example")
        popen.assert_called_once()
        resolver.assert_called_once()
        self.assertIn('server: "8.8.8.8"', configs[0])
        self.assertIn('servername: "tls.example"', configs[0])
        self.assertNotIn('servername: "edge.example"', configs[0])
        self.assertEqual(result["endpoint-fingerprint"]["err"], "http:403")

    def test_run_batch_closes_temporary_config_file(self):
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always", ResourceWarning)
            self.run_batch_with_dns("8.8.8.8", [])
        resource_warnings = [w for w in emitted
                             if issubclass(w.category, ResourceWarning)]
        self.assertEqual(resource_warnings, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
