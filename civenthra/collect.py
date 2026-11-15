"""Explicit, fail-closed collection CLI for reviewed public or licensed books.

This module is intentionally separate from the offline answer service. It never
uses browser sessions, cookies, credentials, paywalls, DRM bypasses, or hidden
source configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .sources import (SourceRegistryError, host_allowed, load_registry,
                      source_by_id, validate_collection_manifest)

USER_AGENT = "Civenthra-public-collector/0.1 (+https://github.com/civenthra/civenthra)"
OAI_NS = "http://www.openarchives.org/OAI/2.0/"
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0


class CollectionError(RuntimeError):
    """A collection request violated policy or failed safely."""


class GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: list[str]):
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise CollectionError("redirect target must use credential-free HTTPS")
        if not host_allowed(target, self.allowed_hosts):
            raise CollectionError("redirect target is outside the source host allowlist")
        return super().redirect_request(req, fp, code, msg, headers, target)


def _opener(allowed_hosts: list[str]):
    return urllib.request.build_opener(GuardedRedirectHandler(allowed_hosts))


def _request(url: str):
    return urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/csv,application/json,*/*;q=0.1"},
    )


def _temporary_path(destination: Path) -> Path:
    fd, name = tempfile.mkstemp(
        prefix=destination.name + ".", suffix=".part", dir=destination.parent
    )
    os.close(fd)
    return Path(name)


def _write_json_atomic(destination: Path, payload: dict) -> None:
    if destination.exists():
        raise CollectionError(f"refusing to overwrite existing evidence: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(destination)
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, destination)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise CollectionError(f"cannot write evidence receipt: {exc}") from exc


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resume_receipt(target: Path, receipt_path: Path, item: dict) -> dict | None:
    target_exists = target.is_file()
    receipt_exists = receipt_path.is_file()
    if not target_exists and not receipt_exists:
        return None
    if target_exists != receipt_exists:
        raise CollectionError("incomplete prior download evidence; refusing unsafe resume")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CollectionError("prior download receipt is invalid") from exc
    expected = {
        "schema": 1,
        "source_id": item["source_id"],
        "item_id": item["item_id"],
        "url": item["download_url"],
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise CollectionError("prior download receipt does not match the requested item")
    if receipt.get("sha256") != _file_sha256(target):
        raise CollectionError("prior download no longer matches its SHA-256 receipt")
    resumed = dict(receipt)
    resumed["resume_status"] = "already_verified"
    return resumed


def _read_response(response, max_bytes: int) -> bytes:
    raw_length = response.headers.get("Content-Length")
    if raw_length:
        try:
            if int(raw_length) > max_bytes:
                raise CollectionError("response exceeds the configured size limit")
        except ValueError:
            pass
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(1024 * 1024, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise CollectionError("response exceeds the configured size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def download_url(url: str, destination: Path, *, allowed_hosts: list[str],
                 max_bytes: int = DEFAULT_MAX_BYTES, timeout: float = DEFAULT_TIMEOUT,
                 opener=None) -> dict:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise CollectionError("downloads require a credential-free HTTPS URL")
    if not host_allowed(url, allowed_hosts):
        raise CollectionError("download host is not allowlisted")
    if max_bytes <= 0 or timeout <= 0:
        raise CollectionError("max_bytes and timeout must be positive")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise CollectionError(f"refusing to overwrite existing download: {destination}")
    temporary = _temporary_path(destination)
    client = opener or _opener(allowed_hosts)
    digest = hashlib.sha256()
    size = 0
    final_url = url
    content_type = ""
    try:
        with client.open(_request(url), timeout=timeout) as response, open(temporary, "wb") as stream:
            final_url = response.geturl()
            final_parsed = urllib.parse.urlsplit(final_url)
            if (final_parsed.scheme != "https" or not final_parsed.hostname
                    or final_parsed.username or final_parsed.password):
                raise CollectionError("final response must use credential-free HTTPS")
            if not host_allowed(final_url, allowed_hosts):
                raise CollectionError("final response host is not allowlisted")
            content_type = response.headers.get("Content-Type", "")
            raw_length = response.headers.get("Content-Length")
            if raw_length:
                try:
                    if int(raw_length) > max_bytes:
                        raise CollectionError("response exceeds the configured size limit")
                except ValueError:
                    pass
            while True:
                chunk = response.read(min(1024 * 1024, max_bytes - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise CollectionError("response exceeds the configured size limit")
                digest.update(chunk)
                stream.write(chunk)
        os.replace(temporary, destination)
    except (CollectionError, OSError, urllib.error.URLError) as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, CollectionError):
            raise
        raise CollectionError(f"download failed: {exc}") from exc
    return {
        "url": url,
        "final_url": final_url,
        "bytes": size,
        "sha256": digest.hexdigest(),
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "content_type": content_type,
        "path": str(destination),
    }


def sync_snapshot(source: dict, destination: Path, *, max_bytes: int, timeout: float,
                  opener=None) -> dict:
    receipt_path = destination.with_name(destination.name + ".receipt.json")
    if receipt_path.exists():
        raise CollectionError(f"refusing to overwrite existing evidence: {receipt_path}")
    receipt = download_url(
        source["catalog_url"], destination, allowed_hosts=source["allowed_hosts"],
        max_bytes=max_bytes, timeout=timeout, opener=opener,
    )
    receipt.update({
        "schema": 1,
        "source_id": source["id"],
        "asset_kind": "catalog_metadata",
        "rights_status": "metadata_only_not_content_clearance",
    })
    _write_json_atomic(receipt_path, receipt)
    return receipt


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _oai_record(element: ET.Element) -> dict:
    header = element.find(f"{{{OAI_NS}}}header")
    if header is None:
        raise CollectionError("OAI-PMH record is missing a header")
    item = {
        "identifier": header.findtext(f"{{{OAI_NS}}}identifier", default=""),
        "datestamp": header.findtext(f"{{{OAI_NS}}}datestamp", default=""),
        "set_specs": [node.text or "" for node in header.findall(f"{{{OAI_NS}}}setSpec")],
        "deleted": header.get("status") == "deleted",
        "metadata": {},
    }
    metadata = element.find(f"{{{OAI_NS}}}metadata")
    if metadata is not None:
        flattened: dict[str, list[str]] = {}
        for node in metadata.iter():
            if node is metadata or not (node.text or "").strip():
                continue
            flattened.setdefault(_local_name(node.tag), []).append((node.text or "").strip())
        item["metadata"] = flattened
    return item


def harvest_oai(source: dict, destination: Path, *, limit: int = 100,
                max_pages: int = 10, max_bytes: int = DEFAULT_MAX_BYTES,
                timeout: float = DEFAULT_TIMEOUT, opener=None,
                sleep=time.sleep) -> dict:
    if limit < 0 or max_pages <= 0:
        raise CollectionError("limit must be non-negative and max_pages must be positive")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    receipt_path = destination.with_name(destination.name + ".receipt.json")
    if destination.exists() or receipt_path.exists():
        raise CollectionError("refusing to overwrite an existing OAI harvest or receipt")
    temporary = _temporary_path(destination)
    client = opener or _opener(source["allowed_hosts"])
    count = 0
    pages = 0
    token = ""
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
            while pages < max_pages and (limit == 0 or count < limit):
                query = ({"verb": "ListRecords", "resumptionToken": token} if token else
                         {"verb": "ListRecords", "metadataPrefix": source["catalog_format"]})
                if not token and source.get("oai_set"):
                    query["set"] = source["oai_set"]
                url = source["catalog_url"] + ("&" if "?" in source["catalog_url"] else "?")
                url += urllib.parse.urlencode(query)
                if not host_allowed(url, source["allowed_hosts"]):
                    raise CollectionError("OAI-PMH endpoint host is not allowlisted")
                with client.open(_request(url), timeout=timeout) as response:
                    final_url = response.geturl()
                    final_parsed = urllib.parse.urlsplit(final_url)
                    if (final_parsed.scheme != "https" or not final_parsed.hostname
                            or final_parsed.username or final_parsed.password):
                        raise CollectionError("OAI-PMH final response must use credential-free HTTPS")
                    if not host_allowed(final_url, source["allowed_hosts"]):
                        raise CollectionError("OAI-PMH final host is not allowlisted")
                    body = _read_response(response, max_bytes)
                    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
                        raise CollectionError("OAI-PMH XML declarations/entities are refused")
                    root = ET.fromstring(body)
                error = root.find(f"{{{OAI_NS}}}error")
                if error is not None:
                    raise CollectionError(f"OAI-PMH error {error.get('code', 'unknown')}: {(error.text or '').strip()}")
                records = root.findall(f".//{{{OAI_NS}}}record")
                for record in records:
                    stream.write(json.dumps(_oai_record(record), ensure_ascii=False) + "\n")
                    count += 1
                    if limit and count >= limit:
                        break
                pages += 1
                token_node = root.find(f".//{{{OAI_NS}}}resumptionToken")
                token = (token_node.text or "").strip() if token_node is not None else ""
                if not token or (limit and count >= limit):
                    break
                sleep(float(source["rate_limit_seconds"]))
        os.replace(temporary, destination)
    except (CollectionError, OSError, urllib.error.URLError, ET.ParseError) as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, CollectionError):
            raise
        raise CollectionError(f"OAI-PMH harvest failed: {exc}") from exc
    receipt = {
        "schema": 1,
        "source_id": source["id"],
        "asset_kind": "catalog_metadata",
        "rights_status": "metadata_only_not_content_clearance",
        "records": count,
        "pages": pages,
        "bytes": destination.stat().st_size,
        "sha256": _file_sha256(destination),
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "path": str(destination),
    }
    _write_json_atomic(receipt_path, receipt)
    return receipt


def fetch_manifest(manifest_path: Path, staging_dir: Path, *, registry: dict,
                   max_bytes: int, timeout: float, opener_factory=None,
                   sleep=time.sleep) -> list[dict]:
    try:
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CollectionError("cannot read collection manifest") from exc
    validate_collection_manifest(payload, registry)
    receipts = []
    staging_root = Path(staging_dir).resolve()
    for item in payload["items"]:
        source = source_by_id(item["source_id"], registry)
        target = (staging_root / source["id"] / item["item_id"] / item["filename"]).resolve()
        if not target.is_relative_to(staging_root):
            raise CollectionError("download target escapes the staging directory")
        receipt_path = target.with_name(target.name + ".receipt.json")
        existing = _resume_receipt(target, receipt_path, item)
        if existing is not None:
            receipts.append(existing)
            continue
        opener = opener_factory(source) if opener_factory else None
        receipt = download_url(
            item["download_url"], target, allowed_hosts=source["allowed_hosts"],
            max_bytes=max_bytes, timeout=timeout, opener=opener,
        )
        receipt.update({
            "schema": 1,
            "source_id": source["id"],
            "item_id": item["item_id"],
            "title": item["title"],
            "territory": payload["territory"],
            "access": item["access"],
            "rights": item["rights"],
            "quarantine_status": "awaiting_human_import_approval",
        })
        _write_json_atomic(receipt_path, receipt)
        receipts.append(receipt)
        sleep(float(source["rate_limit_seconds"]))
    return receipts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="civenthra-collect",
        description="Collect reviewed open/public books into a local quarantine directory",
    )
    parser.add_argument("--registry", type=Path, default=None, help="alternate reviewed source registry")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="list reviewed public metadata sources")
    list_parser.add_argument("--json", action="store_true", help="print machine-readable JSON")

    sync_parser = sub.add_parser("sync-catalog", help="download/harvest source metadata only")
    sync_parser.add_argument("source")
    sync_parser.add_argument("--output", required=True, type=Path)
    sync_parser.add_argument("--limit", type=int, default=100, help="OAI record limit; 0 means unlimited")
    sync_parser.add_argument("--max-pages", type=int, default=10)
    sync_parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    sync_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)

    fetch_parser = sub.add_parser("fetch", help="download reviewed item URLs into quarantine")
    fetch_parser.add_argument("manifest", type=Path)
    fetch_parser.add_argument("--staging-dir", required=True, type=Path)
    fetch_parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    fetch_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)

    args = parser.parse_args(argv)
    try:
        registry = load_registry(args.registry)
        if args.command == "list":
            if args.json:
                print(json.dumps(registry, ensure_ascii=False, indent=2))
            else:
                for source in registry["sources"]:
                    print(f"{source['id']:<22} {source['adapter']:<9} {source['name']}")
                    print(f"  catalog: {source['catalog_url']}")
                    print(f"  policy:  {source['rights_policy_url']}")
            return 0
        if args.command == "sync-catalog":
            source = source_by_id(args.source, registry)
            if source["adapter"] == "snapshot":
                result = sync_snapshot(
                    source, args.output, max_bytes=args.max_bytes, timeout=args.timeout
                )
            else:
                result = harvest_oai(
                    source, args.output, limit=args.limit, max_pages=args.max_pages,
                    max_bytes=args.max_bytes, timeout=args.timeout,
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        results = fetch_manifest(
            args.manifest, args.staging_dir, registry=registry,
            max_bytes=args.max_bytes, timeout=args.timeout,
        )
        print(json.dumps({"downloaded": len(results), "receipts": results}, ensure_ascii=False, indent=2))
        return 0
    except (CollectionError, SourceRegistryError) as exc:
        parser.exit(2, f"collection refused: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
