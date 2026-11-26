#!/usr/bin/env python3
"""Offline regressions for source URL syntax and YAML structure boundaries."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sources_io  # noqa: E402


def valid_doc():
    return {
        "groups": [{"id": "group-1", "title": "Group", "kern": "Kernel",
                    "clients": "Client"}],
        "sources": [{
            "id": "source-1", "name": "Source One", "group": "group-1",
            "url": "https://example.invalid/sub", "type": "original",
            "format": "mixed", "enabled": True,
            "card": {"tag": "Test"}, "mirrors": [],
        }],
    }


class SourceRegistrationValidationTests(unittest.TestCase):
    def test_accepts_http_schemes_and_allowed_ports_without_dns_lookup(self):
        for url in (
            "HTTP://example.invalid/sub",
            "https://example.invalid/sub",
            "http://example.invalid:80/sub",
            "https://example.invalid:443/sub",
        ):
            with self.subTest(url=url):
                doc = valid_doc()
                doc["sources"][0]["url"] = url
                with patch.object(sources_io.netsafe.socket, "getaddrinfo",
                                  side_effect=AssertionError("URL registration must not use DNS")):
                    self.assertTrue(sources_io.validate(doc))

    def test_rejects_non_http_urls_credentials_and_non_allowlisted_ports(self):
        for url in (
            "httpsnot-a-url",
            "file:///tmp/sub",
            "ftp://example.invalid/sub",
            "http:///missing-host",
            "http://example.invalid:22/sub",
            "http://example.invalid:99999/sub",
            "https://user:password@example.invalid/sub",
        ):
            with self.subTest(url=url):
                doc = valid_doc()
                doc["sources"][0]["url"] = url
                with self.assertRaisesRegex(sources_io.SourceError, "source-1|URL|url"):
                    sources_io.validate(doc)

    def test_validates_mirror_urls_with_the_same_syntax_policy(self):
        doc = valid_doc()
        doc["sources"][0]["mirrors"] = ["file:///tmp/mirror"]
        with self.assertRaisesRegex(sources_io.SourceError, "source-1|mirror|URL|url"):
            sources_io.validate(doc)

    def test_rejects_non_list_top_level_collections_as_source_errors(self):
        for field, value in (("groups", {"id": "group-1"}), ("sources", None)):
            with self.subTest(field=field):
                doc = valid_doc()
                doc[field] = value
                with self.assertRaisesRegex(sources_io.SourceError, field):
                    sources_io.validate(doc)

    def test_rejects_non_mapping_group_and_source_entries_with_location(self):
        for field in ("groups", "sources"):
            with self.subTest(field=field):
                doc = valid_doc()
                doc[field] = [None]
                with self.assertRaisesRegex(sources_io.SourceError, field):
                    sources_io.validate(doc)

    def test_reports_missing_group_fields_without_keyerror(self):
        doc = valid_doc()
        doc["groups"] = [{"title": "Group", "kern": "Kernel", "clients": "Client"}]
        with self.assertRaisesRegex(sources_io.SourceError, "groups.*id|id.*groups"):
            sources_io.validate(doc)

    def test_rejects_unhashable_or_non_string_source_identity_as_source_error(self):
        doc = valid_doc()
        doc["sources"][0]["id"] = ["source-1"]
        with self.assertRaisesRegex(sources_io.SourceError, "source-1|id|sources"):
            sources_io.validate(doc)

    def test_does_not_mutate_input_document_when_validation_fails(self):
        doc = valid_doc()
        doc["sources"][0]["url"] = "file:///tmp/sub"
        before = copy.deepcopy(doc)
        with self.assertRaises(sources_io.SourceError):
            sources_io.validate(doc)
        self.assertEqual(doc, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
