import hashlib
import importlib.util
import json
import re
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


def load_script(name):
    path = ROOT / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest_paths():
    return {
        line.strip() for line in (ROOT / "release-manifest.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_sbom_covers_exact_release_manifest():
    sbom = load_script("build_sbom").document(require_clean=False)
    files = {item["fileName"].removeprefix("./") for item in sbom["files"]}
    assert sbom["spdxVersion"] == "SPDX-2.3"
    assert files == manifest_paths()
    assert len(sbom["relationships"]) == len(files)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    from civenthra import __version__
    assert __version__ == version
    assert sbom["packages"][0]["versionInfo"] == version
    assert sbom["packages"][0]["externalRefs"][0]["referenceLocator"].endswith("@" + version)
    assert "version: " + version in (ROOT / "CITATION.cff").read_text(encoding="utf-8")


def test_sbom_refuses_to_mix_head_identity_with_dirty_files(monkeypatch):
    module = load_script("build_sbom")
    monkeypatch.setattr(module, "git_status", lambda: [" M README.md"])
    with pytest.raises(RuntimeError, match="dirty tree"):
        module.document()


def test_checksum_writer_uses_sha256(tmp_path):
    module = load_script("build_checksums")
    artifact = tmp_path / "artifact.whl"
    artifact.write_bytes(b"release artifact")
    assert module.digest(artifact) == hashlib.sha256(b"release artifact").hexdigest()


def test_workflow_actions_are_commit_pinned_and_permissions_are_explicit():
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert re.search(r"(?m)^permissions:\s*$", text), path
        for target in re.findall(r"uses:\s*([^\s#]+)", text):
            revision = target.rsplit("@", 1)[-1]
            assert re.fullmatch(r"[0-9a-f]{40}", revision), (path, target)


def test_dev_lock_is_exact_and_core_has_no_runtime_dependency():
    requirements = []
    current = ""
    for raw in (ROOT / "requirements-dev.lock").read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        current += line.removesuffix("\\").strip() + " "
        if not line.endswith("\\"):
            requirements.append(current.strip())
            current = ""
    assert requirements and not current
    assert all("==" in item and "--hash=sha256:" in item for item in requirements)
    assert any(item.startswith("setuptools==84.2.1 ") for item in requirements)
    assert any(item.startswith("wheel==0.48.0 ") for item in requirements)
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject

    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = workflow.read_text(encoding="utf-8")
        if "requirements-dev.lock" in text:
            assert "--require-hashes -r requirements-dev.lock" in text


def test_root_virtual_environment_variants_are_ignored():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/.venv-*/" in gitignore
