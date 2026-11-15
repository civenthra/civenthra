import copy
import json
from datetime import date

import pytest

from civenthra.sources import (SourceRegistryError, load_registry,
                               host_allowed, validate_collection_manifest,
                               validate_registry)


def reviewed_item(**overrides):
    item = {
        "source_id": "project-gutenberg",
        "item_id": "11",
        "title": "Alice's Adventures in Wonderland",
        "download_url": "https://www.gutenberg.org/cache/epub/11/pg11.txt",
        "filename": "pg11.txt",
        "access": {
            "restricted": False,
            "dark": False,
            "borrow_only": False,
            "drm": False,
        },
        "rights": {
            "basis": "public-domain",
            "territories": ["US"],
            "evidence_url": "https://www.gutenberg.org/policy/license",
            "reviewed_by": "local-operator",
            "reviewed_on": "2026-01-01",
        },
    }
    item.update(overrides)
    return item


def test_default_public_source_registry_is_strict_and_loadable():
    registry = load_registry()
    assert registry["policy"]["authentication_allowed"] is False
    assert {source["id"] for source in registry["sources"]} >= {
        "project-gutenberg", "doab", "internet-archive-public",
        "wikisource-en", "wikisource-zh",
    }
    assert all(source["item_level_rights_review"] is True for source in registry["sources"])
    assert host_allowed("https://archive.org/item", ["archive.org"])
    assert not host_allowed("https://evil.archive.org/item", ["archive.org"])


def test_registry_rejects_http_duplicate_ids_and_credentials():
    registry = load_registry()
    payload = copy.deepcopy(registry)
    payload["sources"][0]["homepage"] = "http://example.invalid/"
    with pytest.raises(SourceRegistryError):
        validate_registry(payload)

    payload = copy.deepcopy(registry)
    payload["sources"].append(copy.deepcopy(payload["sources"][0]))
    with pytest.raises(SourceRegistryError, match="duplicate"):
        validate_registry(payload)

    payload = copy.deepcopy(registry)
    payload["sources"][0]["api_token"] = "secret"
    with pytest.raises(SourceRegistryError, match="credentials"):
        validate_registry(payload)


def test_reviewed_manifest_is_accepted_for_matching_territory():
    payload = {"schema": 1, "territory": "US", "items": [reviewed_item()]}
    assert validate_collection_manifest(payload, today=date(2026, 6, 1)) is payload


@pytest.mark.parametrize("mutation", [
    lambda item: item["access"].update(restricted=True),
    lambda item: item["access"].update(dark=True),
    lambda item: item["access"].update(borrow_only=True),
    lambda item: item["access"].update(drm=True),
    lambda item: item.update(filename="book_lcp.epub"),
    lambda item: item.update(download_url="https://evil.invalid/book.txt"),
    lambda item: item["rights"].update(territories=["GB"]),
    lambda item: item["rights"].update(reviewed_on="2099-01-01"),
])
def test_manifest_refuses_restricted_encrypted_unreviewed_or_wrong_host(mutation):
    item = reviewed_item()
    mutation(item)
    payload = {"schema": 1, "territory": "US", "items": [item]}
    with pytest.raises(SourceRegistryError):
        validate_collection_manifest(payload, today=date(2026, 6, 1))


def test_registry_file_with_secret_field_is_rejected(tmp_path):
    payload = load_registry()
    payload = copy.deepcopy(payload)
    payload["policy"]["cookie_file"] = "private.txt"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SourceRegistryError, match="credentials"):
        load_registry(path)
