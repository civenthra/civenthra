"""Validated registry and per-item rights manifests for public collectors."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

DEFAULT_REGISTRY = Path(__file__).with_name("public_sources.json")
SOURCE_FIELDS = {
    "id", "name", "homepage", "catalog_url", "adapter", "catalog_format",
    "rights_policy_url", "automation_policy_url", "content_access",
    "allowed_hosts", "rate_limit_seconds", "item_level_rights_review", "notes",
}
REQUIRED_SOURCE_FIELDS = SOURCE_FIELDS - {"notes"}
ADAPTERS = {"snapshot", "oai-pmh"}
RIGHTS_BASES = {"public-domain", "open-license", "explicit-permission"}
FORBIDDEN_KEY_PARTS = {"cookie", "credential", "password", "secret", "token", "api_key"}
FORBIDDEN_SUFFIXES = {
    ".bat", ".cmd", ".com", ".dll", ".exe", ".js", ".msi", ".ps1", ".py",
    ".scr", ".vbs",
}
ENCRYPTED_HINTS = ("_lcp.", ".lcp", "encrypted", "_drm")


class SourceRegistryError(ValueError):
    """The public source registry or a collection manifest is unsafe/invalid."""


def _https(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SourceRegistryError(f"{field} must be a non-empty HTTPS URL")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise SourceRegistryError(f"{field} must be a credential-free HTTPS URL")
    return value


def _forbidden_keys(value: object, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
                raise SourceRegistryError(f"credentials are forbidden in public registry: {path}.{key}")
            _forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _forbidden_keys(child, f"{path}[{index}]")


def validate_registry(payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise SourceRegistryError("source registry must use schema 1")
    _forbidden_keys(payload)
    policy = payload.get("policy")
    if not isinstance(policy, dict):
        raise SourceRegistryError("source registry policy is required")
    if policy.get("authentication_allowed") is not False:
        raise SourceRegistryError("public source credentials must be disabled")
    if policy.get("restricted_access_allowed") is not False:
        raise SourceRegistryError("restricted-access collection must be disabled")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise SourceRegistryError("source registry must contain at least one source")

    ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise SourceRegistryError("source entries must be objects")
        unknown = set(source) - SOURCE_FIELDS
        missing = REQUIRED_SOURCE_FIELDS - set(source)
        if unknown or missing:
            raise SourceRegistryError(
                f"source fields invalid; missing={sorted(missing)} unknown={sorted(unknown)}"
            )
        source_id = source["id"]
        if not isinstance(source_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", source_id):
            raise SourceRegistryError("source id must be a lowercase slug")
        if source_id in ids:
            raise SourceRegistryError(f"duplicate source id: {source_id}")
        ids.add(source_id)
        for field in ("homepage", "catalog_url", "rights_policy_url", "automation_policy_url"):
            _https(source[field], f"{source_id}.{field}")
        if source["adapter"] not in ADAPTERS:
            raise SourceRegistryError(f"unsupported adapter for {source_id}")
        hosts = source["allowed_hosts"]
        if (not isinstance(hosts, list) or not hosts
                or any(not isinstance(host, str) or host != host.lower() for host in hosts)):
            raise SourceRegistryError(f"allowed_hosts invalid for {source_id}")
        for field in ("homepage", "catalog_url"):
            if not host_allowed(source[field], hosts):
                raise SourceRegistryError(f"{source_id}.{field} host is not allowlisted")
        delay = source["rate_limit_seconds"]
        if not isinstance(delay, (int, float)) or isinstance(delay, bool) or delay < 0:
            raise SourceRegistryError(f"rate_limit_seconds invalid for {source_id}")
        if source["item_level_rights_review"] is not True:
            raise SourceRegistryError(f"item-level rights review must be enabled for {source_id}")
    return payload


def load_registry(path: Path | str | None = None) -> dict:
    registry_path = Path(path) if path else DEFAULT_REGISTRY
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SourceRegistryError(f"cannot read source registry: {registry_path}") from exc
    return validate_registry(payload)


def source_by_id(source_id: str, registry: dict | None = None) -> dict:
    registry = registry or load_registry()
    for source in registry["sources"]:
        if source["id"] == source_id:
            return source
    raise SourceRegistryError(f"unknown source: {source_id}")


def host_allowed(url: str, allowed_hosts: list[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in allowed_hosts


def validate_collection_manifest(payload: object, registry: dict | None = None,
                                 *, today: date | None = None) -> dict:
    registry = registry or load_registry()
    today = today or date.today()
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise SourceRegistryError("collection manifest must use schema 1")
    if set(payload) != {"schema", "territory", "items"}:
        raise SourceRegistryError("collection manifest fields must be schema, territory, items")
    territory = payload["territory"]
    if not isinstance(territory, str) or not re.fullmatch(r"[A-Z]{2}", territory):
        raise SourceRegistryError("territory must be an ISO 3166-1 alpha-2 code")
    items = payload["items"]
    if not isinstance(items, list) or not items:
        raise SourceRegistryError("collection manifest must contain items")

    seen: set[tuple[str, str]] = set()
    for item in items:
        required = {
            "source_id", "item_id", "title", "download_url", "filename", "access", "rights"
        }
        if not isinstance(item, dict) or set(item) != required:
            raise SourceRegistryError("collection item fields are invalid")
        source = source_by_id(item["source_id"], registry)
        item_id = item["item_id"]
        if (not isinstance(item_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", item_id)):
            raise SourceRegistryError("item_id is unsafe")
        key = (source["id"], item_id)
        if key in seen:
            raise SourceRegistryError(f"duplicate item: {source['id']}/{item_id}")
        seen.add(key)
        if not isinstance(item["title"], str) or not item["title"].strip():
            raise SourceRegistryError("item title is required")
        _https(item["download_url"], "download_url")
        if not host_allowed(item["download_url"], source["allowed_hosts"]):
            raise SourceRegistryError("download host is not allowlisted for the source")
        filename = item["filename"]
        if (not isinstance(filename, str)
                or PurePosixPath(filename).name != filename
                or PureWindowsPath(filename).name != filename
                or not filename.strip() or Path(filename).suffix.lower() in FORBIDDEN_SUFFIXES):
            raise SourceRegistryError("download filename is unsafe")
        encrypted_text = (filename + " " + item["download_url"]).lower()
        if any(hint in encrypted_text for hint in ENCRYPTED_HINTS):
            raise SourceRegistryError("encrypted, LCP, or DRM-looking files are refused")

        access = item["access"]
        access_fields = {"restricted", "dark", "borrow_only", "drm"}
        if (not isinstance(access, dict) or set(access) != access_fields
                or any(access[field] is not False for field in access_fields)):
            raise SourceRegistryError("restricted, dark, borrow-only, or DRM items are refused")

        rights = item["rights"]
        rights_fields = {"basis", "territories", "evidence_url", "reviewed_by", "reviewed_on"}
        if not isinstance(rights, dict) or set(rights) != rights_fields:
            raise SourceRegistryError("item rights evidence is incomplete")
        if rights["basis"] not in RIGHTS_BASES:
            raise SourceRegistryError("unsupported rights basis")
        territories = rights["territories"]
        if not isinstance(territories, list) or territory not in territories:
            raise SourceRegistryError("item rights do not cover the collection territory")
        _https(rights["evidence_url"], "rights.evidence_url")
        if not isinstance(rights["reviewed_by"], str) or not rights["reviewed_by"].strip():
            raise SourceRegistryError("rights reviewer is required")
        try:
            reviewed_on = date.fromisoformat(rights["reviewed_on"])
        except (TypeError, ValueError) as exc:
            raise SourceRegistryError("rights.reviewed_on must use YYYY-MM-DD") from exc
        if reviewed_on > today:
            raise SourceRegistryError("rights review date cannot be in the future")
    return payload
