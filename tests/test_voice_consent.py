import unittest
from pathlib import Path


class VoiceConsentTest(unittest.TestCase):
    def test_public_voice_clone_requires_explicit_authorization(self):
        source = (Path(__file__).parents[1] / "xiaoya_tts.py").read_text(encoding="utf-8")
        self.assertIn("require_authorization(", source)
        self.assertIn('"xtts-v2-voice-cloning"', source)
        self.assertIn('required_constraints=("non-commercial", "no-redistribution")', source)
        self.assertNotIn("XIAOYA_VOICE_CONSENT", source)
        self.assertIn("if not is_loopback_host(a.host)", source)
        self.assertIn("math.isfinite(float(speed))", source)
        self.assertNotIn('"ref_wav": REF_WAV', source)
        self.assertNotIn('"tts_home": TTS_HOME', source)
        self.assertNotIn('os.environ.setdefault("COQUI_TOS_AGREED"', source)

    def test_voice_sidecars_refuse_non_loopback_bindings(self):
        root = Path(__file__).parents[1]
        for filename in ("voicebox.py", "xiaoya_tts.py"):
            source = (root / filename).read_text(encoding="utf-8")
            self.assertIn("if not is_loopback_host(a.host)", source)

    def test_sapi_uses_unique_cleaned_temporary_files(self):
        source = (Path(__file__).parents[1] / "voicebox.py").read_text(encoding="utf-8")
        self.assertIn('tempfile.mkstemp(prefix="civenthra-sapi-"', source)
        self.assertNotIn("_voicebox_sapi.wav", source)
        self.assertIn("os.remove(tmp)", source)


if __name__ == "__main__":
    unittest.main()
