import json
from datetime import date

import pytest

from civenthra.authorizations import AuthorizationError, require_authorization


def approved_record(component="minimax-h3"):
    return {
        "schema": 1,
        "records": {
            component: {
                "authorization_id": "review-001",
                "component": component,
                "status": "approved",
                "subject_id": "reviewed-subject",
                "scopes": ["local-educational-scene-generation"],
                "territories": ["CN"],
                "constraints": ["territory-reviewed", "no-redistribution"],
                "license": {
                    "name": "Reviewed license",
                    "version": "1.0",
                    "source_uri": "https://example.invalid/license",
                    "sha256": "a" * 64,
                },
                "reviewer_id": "reviewer-001",
                "reviewed_on": "2026-01-01",
                "expires_on": "2026-12-31",
                "evidence_ref": "private-evidence-001",
            }
        },
    }


def write_record(tmp_path, payload):
    path = tmp_path / "authorizations.local.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_current_record_is_accepted(tmp_path):
    path = write_record(tmp_path, approved_record())
    record = require_authorization(
        "minimax-h3", "local-educational-scene-generation", "CN",
        path=path, required_constraints=("territory-reviewed",),
        today=date(2026, 6, 1),
    )
    assert record["authorization_id"] == "review-001"


@pytest.mark.parametrize("mutation", [
    lambda record: record.update(status="pending"),
    lambda record: record.update(expires_on="2025-12-31"),
    lambda record: record.update(territories=["US"]),
    lambda record: record["license"].update(sha256="not-a-hash"),
    lambda record: record["license"].update(sha256="A" * 64),
])
def test_incomplete_stale_or_mismatched_record_is_rejected(tmp_path, mutation):
    payload = approved_record()
    mutation(payload["records"]["minimax-h3"])
    path = write_record(tmp_path, payload)
    with pytest.raises(AuthorizationError):
        require_authorization(
            "minimax-h3", "local-educational-scene-generation", "CN",
            path=path, required_constraints=("territory-reviewed",),
            today=date(2026, 6, 1),
        )


def test_voice_record_is_bound_to_reference_audio(tmp_path):
    payload = approved_record("xtts-v2-voice-cloning")
    record = payload["records"]["xtts-v2-voice-cloning"]
    record["scopes"] = ["local-noncommercial-voice-cloning"]
    record["constraints"] = ["non-commercial", "no-redistribution"]
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"authorized synthetic fixture")
    import hashlib
    record["input_sha256"] = hashlib.sha256(reference.read_bytes()).hexdigest()
    path = write_record(tmp_path, payload)
    require_authorization(
        "xtts-v2-voice-cloning", "local-noncommercial-voice-cloning", "CN",
        path=path, artifact=reference, required_constraints=("non-commercial",),
        today=date(2026, 6, 1),
    )
    reference.write_bytes(b"changed")
    with pytest.raises(AuthorizationError):
        require_authorization(
            "xtts-v2-voice-cloning", "local-noncommercial-voice-cloning", "CN",
            path=path, artifact=reference, required_constraints=("non-commercial",),
            today=date(2026, 6, 1),
        )
