#!/usr/bin/env python3
"""P24 offline regression tests for parser port and transport boundaries."""
import base64
import json
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import nodes_parse  # noqa: E402


UUID = "123e4567-e89b-12d3-a456-426614174000"
SERVER = "node.example.com"
PORT_CASES = (1, 65535, 0, -1, 65536, "not-a-port", True, 1.5)


def vmess_uri(port):
    payload = {
        "v": "2",
        "ps": str(port),
        "add": SERVER,
        "port": port,
        "id": UUID,
        "aid": 0,
        "scy": "auto",
        "net": "tcp",
    }
    encoded = base64.b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return "vmess://" + encoded


def ss_uri(port):
    userinfo = base64.b64encode(b"aes-256-gcm:password").decode()
    return f"ss://{userinfo}@{SERVER}:{port}"


def clash_proxy(name, port, **extra):
    return {
        "name": name,
        "type": "vmess",
        "server": SERVER,
        "port": port,
        "uuid": UUID,
        **extra,
    }


def singbox_outbound(name, port, **extra):
    return {
        "tag": name,
        "type": "vmess",
        "server": SERVER,
        "server_port": port,
        "uuid": UUID,
        **extra,
    }


class NodeParserBoundaryTest(unittest.TestCase):
    def assert_only_valid_port_boundaries(self, data):
        rejects = []
        nodes, raw_count = nodes_parse.parse_any(data, rejects)

        self.assertEqual(raw_count, len(PORT_CASES))
        self.assertEqual([node["port"] for node in nodes], [1, 65535])
        self.assertEqual(len(rejects), len(PORT_CASES) - 2)
        for reject in rejects:
            self.assertNotIn("cred", reject)
            self.assertNotIn("uuid", reject)

    def test_uri_ports_are_limited_to_the_valid_tcp_udp_range(self):
        self.assert_only_valid_port_boundaries(
            "\n".join(vmess_uri(port) for port in PORT_CASES)
        )

    def test_ss_uri_rejects_malformed_port_text_instead_of_extracting_digits(self):
        self.assert_only_valid_port_boundaries(
            "\n".join(ss_uri(port) for port in PORT_CASES)
        )

    def test_clash_ports_are_limited_to_the_valid_tcp_udp_range(self):
        data = yaml.safe_dump(
            {"proxies": [
                clash_proxy(str(port), port) for port in PORT_CASES
            ]},
            sort_keys=False,
        )
        self.assert_only_valid_port_boundaries(data)

    def test_singbox_ports_are_limited_to_the_valid_tcp_udp_range(self):
        data = json.dumps({
            "outbounds": [
                singbox_outbound(str(port), port) for port in PORT_CASES
            ]
        })
        self.assert_only_valid_port_boundaries(data)

    def test_clash_non_string_network_records_are_quarantined_without_losing_peers(self):
        proxies = [
            clash_proxy("integer", 443, network=7),
            clash_proxy("list", 443, network=["ws"]),
            clash_proxy("null", 443, network=None),
            clash_proxy("valid", 443, network="ws"),
            clash_proxy("default", 443),
        ]
        data = yaml.safe_dump({"proxies": proxies}, sort_keys=False)
        rejects = []

        nodes, raw_count = nodes_parse.parse_any(data, rejects)

        self.assertEqual(raw_count, len(proxies))
        self.assertEqual([node["name"] for node in nodes], ["valid", "default"])
        self.assertEqual([node["network"] for node in nodes], ["ws", "tcp"])
        self.assertEqual(len(rejects), 3)
        self.assertTrue(all(item["reason"] == nodes_parse.REASON["bad_record"]
                            for item in rejects))

    def test_singbox_non_string_transport_types_are_quarantined_without_losing_peers(self):
        outbounds = [
            singbox_outbound("integer", 443, transport={"type": 7}),
            singbox_outbound("list", 443, transport={"type": ["ws"]}),
            singbox_outbound("null", 443, transport={"type": None}),
            singbox_outbound("valid", 443, transport={"type": "ws"}),
            singbox_outbound("default", 443),
        ]
        data = json.dumps({"outbounds": outbounds})
        rejects = []

        nodes, raw_count = nodes_parse.parse_any(data, rejects)

        self.assertEqual(raw_count, len(outbounds))
        self.assertEqual([node["name"] for node in nodes], ["valid", "default"])
        self.assertEqual([node["network"] for node in nodes], ["ws", "tcp"])
        self.assertEqual(len(rejects), 3)
        self.assertTrue(all(item["reason"] == nodes_parse.REASON["bad_record"]
                            for item in rejects))


if __name__ == "__main__":
    unittest.main(verbosity=2)
