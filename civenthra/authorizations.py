"""Fail-closed validation for private, operator-maintained authorization records."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
AUTHORIZATION_FILE_ENV = "CIVENTHRA_AUTHORIZATION_FILE"
DEFAULT_AUTHORIZATION_FILE = ROOT / "authorizations.local.json"
_SHA256 = re.compile(r"[0-9a-f]{64}")


class AuthorizationError(RuntimeError):
    """Raised when a restricted integration lacks a current approval record."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nonempty(record: dict, field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AuthorizationError("authorization field %s must be a non-empty string" % field)
    return value.strip()


def _date(record: dict, field: str) -> date:
    value = _nonempty(record, field)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AuthorizationError("authorization field %s must use YYYY-MM-DD" % field) from exc


def _string_set(record: dict, field: str) -> set[str]:
    values = record.get(field)
    if (not isinstance(values, list) or not values
            or any(not isinstance(value, str) or not value.strip() for value in values)):
        raise AuthorizationError("authorization field %s must be a non-empty string list" % field)
    normalized = {value.strip().casefold() for value in values}
    if len(normalized) != len(values):
        raise AuthorizationError("authorization field %s must not contain duplicates" % field)
    return normalized


def require_authorization(
    component: str,
    scope: str,
    territory: str,
    *,
    path: Path | str | None = None,
    artifact: Path | None = None,
    required_constraints: tuple[str, ...] = (),
    today: date | None = None,
) -> dict:
    """Return a verified record or raise before a restricted component is loaded."""
    if not territory.strip():
        raise AuthorizationError("deployment territory must be configured")
    record_path = Path(path or os.environ.get(AUTHORIZATION_FILE_ENV, DEFAULT_AUTHORIZATION_FILE))
    try:
        payload = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthorizationError("authorization record is missing or invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise AuthorizationError("authorization record must use schema 1")
    records = payload.get("records")
    if not isinstance(records, dict) or not isinstance(records.get(component), dict):
        raise AuthorizationError("authorization record is missing component %s" % component)
    record = records[component]
    if record.get("status") != "approved":
        raise AuthorizationError("authorization for %s is not approved" % component)
    if _nonempty(record, "component") != component:
        raise AuthorizationError("authorization component does not match its record key")
    for field in ("authorization_id", "subject_id", "reviewer_id", "evidence_ref"):
        _nonempty(record, field)

    scopes = _string_set(record, "scopes")
    if scope.casefold() not in scopes:
        raise AuthorizationError("authorization does not cover required scope %s" % scope)
    territories = _string_set(record, "territories")
    if territory.casefold() not in territories and "worldwide" not in territories:
        raise AuthorizationError("authorization does not cover deployment territory %s" % territory)
    constraints = _string_set(record, "constraints")
    missing_constraints = {
        item.casefold() for item in required_constraints
    } - constraints
    if missing_constraints:
        raise AuthorizationError("authorization is missing required constraints")

    license_record = record.get("license")
    if not isinstance(license_record, dict):
        raise AuthorizationError("authorization license must be an object")
    for field in ("name", "version", "source_uri"):
        _nonempty(license_record, field)
    source_uri = urlparse(license_record["source_uri"])
    if source_uri.scheme != "https" or not source_uri.netloc:
        raise AuthorizationError("authorization license source_uri must be an HTTPS URL")
    license_hash = _nonempty(license_record, "sha256")
    if license_hash != license_hash.lower() or not _SHA256.fullmatch(license_hash):
        raise AuthorizationError("authorization license sha256 must be 64 lowercase hex characters")

    current = today or date.today()
    reviewed_on = _date(record, "reviewed_on")
    expires_on = _date(record, "expires_on")
    if reviewed_on > current:
        raise AuthorizationError("authorization review date is in the future")
    if expires_on < current:
        raise AuthorizationError("authorization has expired")
    if expires_on < reviewed_on:
        raise AuthorizationError("authorization expiry precedes its review date")

    if artifact is not None:
        if not artifact.is_file():
            raise AuthorizationError("authorized input artifact is missing")
        expected = _nonempty(record, "input_sha256")
        if (expected != expected.lower() or not _SHA256.fullmatch(expected)
                or expected != file_sha256(artifact)):
            raise AuthorizationError("authorized input artifact hash does not match")
    return record
