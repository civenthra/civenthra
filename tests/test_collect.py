import io
import json
import urllib.request

import pytest

from civenthra.collect import (CollectionError, GuardedRedirectHandler,
                               download_url, fetch_manifest, harvest_oai)
from civenthra.sources import load_registry


class FakeResponse:
    def __init__(self, body: bytes, url: str, content_length=None):
        self.body = io.BytesIO(body)
        self.url = url
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def read(self, size=-1):
        return self.body.read(size)

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request.full_url, timeout))
        return self.responses.pop(0)


def test_download_is_atomic_and_records_digest(tmp_path):
    body = b"synthetic public-domain fixture"
    opener = FakeOpener([FakeResponse(body, "https://books.example/item.txt", len(body))])
    target = tmp_path / "item.txt"
    receipt = download_url(
        "https://books.example/item.txt", target,
        allowed_hosts=["books.example"], max_bytes=1024, timeout=3, opener=opener,
    )
    assert target.read_bytes() == body
    assert receipt["bytes"] == len(body)
    assert len(receipt["sha256"]) == 64
    assert not (tmp_path / "item.txt.part").exists()


def test_oversized_or_cross_host_response_fails_without_partial_file(tmp_path):
    target = tmp_path / "item.txt"
    opener = FakeOpener([FakeResponse(b"12345", "https://books.example/item.txt", 500)])
    with pytest.raises(CollectionError, match="size limit"):
        download_url(
            "https://books.example/item.txt", target,
            allowed_hosts=["books.example"], max_bytes=10, opener=opener,
        )
    assert not target.exists()
    assert not (tmp_path / "item.txt.part").exists()

    opener = FakeOpener([FakeResponse(b"ok", "https://evil.example/item.txt")])
    with pytest.raises(CollectionError, match="final response host"):
        download_url(
            "https://books.example/item.txt", target,
            allowed_hosts=["books.example"], opener=opener,
        )
    assert not target.exists()


def test_download_requires_https_and_rejects_xml_entities(tmp_path):
    with pytest.raises(CollectionError, match="HTTPS"):
        download_url(
            "http://books.example/item.txt", tmp_path / "item.txt",
            allowed_hosts=["books.example"], opener=FakeOpener([]),
        )

    malicious = b"<!DOCTYPE x [<!ENTITY e 'boom'>]><OAI-PMH/>"
    source = {
        "id": "test-oai",
        "catalog_url": "https://catalog.example/oai",
        "catalog_format": "oai_dc",
        "allowed_hosts": ["catalog.example"],
        "rate_limit_seconds": 0,
    }
    opener = FakeOpener([FakeResponse(malicious, "https://catalog.example/oai")])
    with pytest.raises(CollectionError, match="entities"):
        harvest_oai(source, tmp_path / "bad.jsonl", opener=opener)
    assert not (tmp_path / "bad.jsonl.part").exists()


def test_redirect_handler_refuses_unlisted_host():
    handler = GuardedRedirectHandler(["books.example"])
    with pytest.raises(CollectionError, match="redirect"):
        handler.redirect_request(
            urllib.request.Request("https://books.example/start"), None,
            302, "Found", {}, "https://evil.example/file",
        )
    with pytest.raises(CollectionError, match="HTTPS"):
        handler.redirect_request(
            urllib.request.Request("https://books.example/start"), None,
            302, "Found", {}, "http://books.example/file",
        )


def test_oai_harvest_writes_normalized_metadata_and_receipt(tmp_path):
    xml = b"""<?xml version='1.0' encoding='UTF-8'?>
    <OAI-PMH xmlns='http://www.openarchives.org/OAI/2.0/'
             xmlns:oai_dc='http://www.openarchives.org/OAI/2.0/oai_dc/'
             xmlns:dc='http://purl.org/dc/elements/1.1/'>
      <ListRecords>
        <record>
          <header><identifier>oai:test:1</identifier><datestamp>2026-01-01</datestamp></header>
          <metadata><oai_dc:dc><dc:title>Synthetic Book</dc:title>
            <dc:rights>CC0</dc:rights></oai_dc:dc></metadata>
        </record>
        <resumptionToken></resumptionToken>
      </ListRecords>
    </OAI-PMH>"""
    source = {
        "id": "test-oai",
        "catalog_url": "https://catalog.example/oai",
        "catalog_format": "oai_dc",
        "allowed_hosts": ["catalog.example"],
        "rate_limit_seconds": 0,
    }
    opener = FakeOpener([FakeResponse(xml, "https://catalog.example/oai")])
    target = tmp_path / "records.jsonl"
    receipt = harvest_oai(
        source, target, limit=10, max_pages=2, max_bytes=4096,
        opener=opener, sleep=lambda _: None,
    )
    record = json.loads(target.read_text(encoding="utf-8"))
    assert record["identifier"] == "oai:test:1"
    assert record["metadata"]["title"] == ["Synthetic Book"]
    assert record["metadata"]["rights"] == ["CC0"]
    assert receipt["records"] == 1
    assert target.with_name("records.jsonl.receipt.json").is_file()


def test_reviewed_batch_resumes_only_when_file_matches_receipt(tmp_path):
    manifest = {
        "schema": 1,
        "territory": "US",
        "items": [{
            "source_id": "project-gutenberg",
            "item_id": "11",
            "title": "Synthetic reviewed title",
            "download_url": "https://www.gutenberg.org/cache/epub/11/pg11.txt",
            "filename": "pg11.txt",
            "access": {
                "restricted": False, "dark": False,
                "borrow_only": False, "drm": False,
            },
            "rights": {
                "basis": "public-domain",
                "territories": ["US"],
                "evidence_url": "https://www.gutenberg.org/policy/license",
                "reviewed_by": "test-reviewer",
                "reviewed_on": "2026-01-01",
            },
        }],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    staging = tmp_path / "staging"
    opener = FakeOpener([
        FakeResponse(b"synthetic reviewed book", manifest["items"][0]["download_url"])
    ])
    first = fetch_manifest(
        manifest_path, staging, registry=load_registry(), max_bytes=1024, timeout=3,
        opener_factory=lambda _: opener, sleep=lambda _: None,
    )
    assert len(first) == 1

    second = fetch_manifest(
        manifest_path, staging, registry=load_registry(), max_bytes=1024, timeout=3,
        opener_factory=lambda _: FakeOpener([]), sleep=lambda _: None,
    )
    assert second[0]["resume_status"] == "already_verified"

    target = staging / "project-gutenberg" / "11" / "pg11.txt"
    target.write_bytes(b"tampered")
    with pytest.raises(CollectionError, match="SHA-256"):
        fetch_manifest(
            manifest_path, staging, registry=load_registry(), max_bytes=1024, timeout=3,
            opener_factory=lambda _: FakeOpener([]), sleep=lambda _: None,
        )
