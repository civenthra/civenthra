import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from civenthra import scene_backend as scene


class SceneBackendTest(unittest.TestCase):
    def test_verified_h3_profile_builds_offline_command(self):
        request = scene.SceneRequest("A synthetic classroom model.", "lesson_scene")
        with patch.object(scene, "h3_paths", return_value={
            "python": Path("python"), "root": Path("WanGP"),
            "config": Path("config"), "settings": Path("settings"),
            "output": Path("out")}), tempfile.TemporaryDirectory() as tmp:
            command = scene.build_h3_command(request, Path(tmp) / "job.json")
        self.assertIn("--offline", command)
        self.assertEqual(scene.h3_job(request)["output_role"], "educational_scene")

    def test_unverified_profiles_are_rejected(self):
        with self.assertRaises(ValueError):
            scene.SceneRequest("test", "bad name").validate()
        with self.assertRaises(ValueError):
            scene.SceneRequest("test", "scene", width=1280, height=720).validate()

    def test_h3_cannot_run_without_structured_authorization(self):
        request = scene.SceneRequest("A synthetic classroom model.", "lesson_scene")
        with patch.object(scene.C, "cfg", side_effect=lambda key, default: "h3" if key == "scene_backend" else default):
            with patch.object(scene.C, "DEPLOYMENT_TERRITORY", "CN"):
                with patch.object(scene.C, "AUTHORIZATION_FILE", Path("missing-authorizations.json")):
                    with self.assertRaisesRegex(RuntimeError, "authorization"):
                        scene.generate_h3(request)


if __name__ == "__main__":
    unittest.main()
