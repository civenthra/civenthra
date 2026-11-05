import importlib.util
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_archive.py"
SPEC = importlib.util.spec_from_file_location("audit_archive", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def wheel(tmp_path, members):
    path = tmp_path / "test.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


def test_safe_wheel_passes(tmp_path):
    path = wheel(tmp_path, {
        "civenthra/__init__.py": "VERSION = 'test'\n",
        "civenthra_local-0.1.dist-info/METADATA": "Name: civenthra-local\n",
    })
    assert audit.findings(path) == []


def test_private_path_and_payload_fail(tmp_path):
    path = wheel(tmp_path, {
        "civenthra/config.py": "ROOT = 'C:/" + "Users/example/private'\n",
        "civenthra/private.pkl": b"payload",
    })
    issues = audit.findings(path)
    assert any("sensitive pattern" in item for item in issues)
    assert any("forbidden format" in item for item in issues)


def test_source_escaped_windows_user_path_fails(tmp_path):
    escaped = "C:" + "\\\\Users\\\\example\\\\private"
    path = wheel(tmp_path, {"civenthra/config.py": f"ROOT = {escaped!r}\n"})
    assert any("sensitive pattern" in item for item in audit.findings(path))


def test_unexpected_root_fails(tmp_path):
    path = wheel(tmp_path, {"private/book.txt": "not allowed"})
    assert any("unexpected root" in item for item in audit.findings(path))


def test_dist_info_substring_does_not_bypass_root_check(tmp_path):
    path = wheel(tmp_path, {"private.dist-info/payload.dat": b"\x00\xff"})
    assert any("unexpected root" in item for item in audit.findings(path))


def test_unknown_package_binary_fails_closed(tmp_path):
    path = wheel(tmp_path, {"civenthra/payload.dat": b"\x00\xff"})
    assert any("unexpected root" in item for item in audit.findings(path))
