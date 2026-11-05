import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import open_source_audit as audit


class OpenSourceAuditTest(unittest.TestCase):
    def test_detects_private_path_secret_and_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private_path = "C:/" + "Users/example/private"
            (root / "unsafe.py").write_text(f"p={private_path!r}\n", encoding="utf-8")
            (root / "empty.md").touch()
            for required in audit.REQUIRED_FILES:
                (root / required).parent.mkdir(parents=True, exist_ok=True)
                (root / required).write_text("fixture", encoding="utf-8")
            with patch.object(audit, "ROOT", root):
                result = audit.audit(["unsafe.py", "empty.md"])
        self.assertIn("privacy.user-path", {item.code for item in result})
        self.assertIn("file.empty", {item.code for item in result})

    def test_detects_source_escaped_windows_and_macos_user_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            escaped = "C:" + "\\\\Users\\\\example\\\\private"
            macos = "/" + "Users/example/private"
            (root / "unsafe.py").write_text(
                f"windows={escaped!r}\nmacos={macos!r}\n", encoding="utf-8"
            )
            for required in audit.REQUIRED_FILES:
                (root / required).parent.mkdir(parents=True, exist_ok=True)
                (root / required).write_text("fixture", encoding="utf-8")
            with patch.object(audit, "ROOT", root):
                result = audit.audit(["unsafe.py"])
        self.assertIn("privacy.user-path", {item.code for item in result})

    def test_safe_release_candidate_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for required in audit.REQUIRED_FILES:
                (root / required).parent.mkdir(parents=True, exist_ok=True)
                (root / required).write_text("safe fixture", encoding="utf-8")
            (root / "module.py").write_text("VALUE = 'local-first'\n", encoding="utf-8")
            with patch.object(audit, "ROOT", root):
                result = audit.audit(["module.py"])
        self.assertEqual(result, [])

    def test_manifest_rejects_unreviewed_and_stale_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / audit.RELEASE_MANIFEST).write_text(
                "reviewed.py\nstale.py\n", encoding="utf-8"
            )
            with patch.object(audit, "ROOT", root):
                result = audit.manifest_findings(["reviewed.py", "unreviewed.py"])
        by_code = {item.code: item.path for item in result}
        self.assertEqual(by_code["release.unreviewed"], "unreviewed.py")
        self.assertEqual(by_code["release.stale"], "stale.py")

    def test_unknown_public_format_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "payload.dat").write_bytes(b"private payload")
            for required in audit.REQUIRED_FILES:
                (root / required).parent.mkdir(parents=True, exist_ok=True)
                (root / required).write_text("fixture", encoding="utf-8")
            with patch.object(audit, "ROOT", root):
                result = audit.audit(["payload.dat"])
        self.assertIn("file.unreviewed-format", {item.code for item in result})

    def test_detector_exemption_is_limited_to_pattern_assignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            detector = root / "scripts" / "open_source_audit.py"
            detector.parent.mkdir(parents=True)
            marker = "sk-" + "A" * 24
            detector.write_text(
                "import re\nPATTERNS = [re.compile(%r)]\nLEAK = %r\n" % (marker, marker),
                encoding="utf-8",
            )
            for required in audit.REQUIRED_FILES:
                path = root / required
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_text("fixture", encoding="utf-8")
            with patch.object(audit, "ROOT", root):
                result = audit.audit(["scripts/open_source_audit.py"])
        self.assertIn("secret.openai", {item.code for item in result})

    def test_detector_pattern_assignment_does_not_self_trigger(self):
        marker = "sk-" + "A" * 24
        source = "import re\nPATTERNS = [re.compile(%r)]\nVALUE = 'safe'\n" % marker
        masked = audit.scan_payload("scripts/open_source_audit.py", source)
        self.assertNotIn(marker, masked)


if __name__ == "__main__":
    unittest.main()
