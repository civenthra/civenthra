"""Optional local scene-generation adapters.

H3 is invoked as an external, separately licensed installation. This module
does not import WanGP, download weights, or expose a network listener. H3 and
WanGP are not relicensed as Civenthra components; callers must independently
confirm current territorial and use restrictions before enabling the adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess

from . import config as C
from .authorizations import require_authorization


@dataclass(frozen=True)
class SceneRequest:
    prompt: str
    name: str
    width: int = 832
    height: int = 480
    frames: int = 124
    fps: int = 24
    steps: int = 20
    seed: int = 42

    def validate(self) -> None:
        if not self.prompt.strip() or len(self.prompt) > 4000:
            raise ValueError("Scene prompt must contain 1-4000 characters")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", self.name):
            raise ValueError("Scene name must be a safe lowercase identifier")
        if (self.width, self.height) != (832, 480):
            raise ValueError("Only the locally verified H3 832x480 profile is enabled")
        if not (1 <= self.frames <= 124 and self.fps == 24 and 1 <= self.steps <= 20):
            raise ValueError("Scene request exceeds the verified local H3 profile")


def h3_paths() -> dict[str, Path]:
    return {
        "python": Path(C.cfg("h3_python", "python")),
        "root": Path(C.cfg("h3_wangp_root", C.ROOT / "vendor" / "WanGP")),
        "config": Path(C.cfg("h3_config_dir", C.ROOT / "config" / "h3")),
        "settings": Path(C.cfg("h3_settings_dir", C.ROOT / ".local" / "h3-settings")),
        "output": Path(C.cfg("h3_output_dir", C.ROOT / "out" / "scenes")),
    }


def h3_job(request: SceneRequest) -> dict:
    request.validate()
    return {
        "image_mode": 0,
        "prompt": request.prompt,
        "resolution": f"{request.width}x{request.height}",
        "video_length": request.frames,
        "force_fps": str(request.fps),
        "num_inference_steps": request.steps,
        "seed": request.seed,
        "output_filename": request.name,
        "model_type": "minimax_h3_fl2va_pruned_comfy",
        "output_role": "educational_scene",
    }


def build_h3_command(request: SceneRequest, job_file: Path) -> list[str]:
    paths = h3_paths()
    request.validate()
    return [str(paths["python"]), str(paths["root"] / "wgp.py"),
            "--config", str(paths["config"]), "--settings", str(paths["settings"]),
            "--process", str(job_file), "--output-dir", str(paths["output"]),
            "--profile", "5", "--attention", "sdpa", "--perc-reserved-mem-max", "0.2",
            "--offline"]


def generate_h3(request: SceneRequest, *, dry_run: bool = False) -> Path:
    """Generate one bounded scene after an explicit application call."""
    if str(C.cfg("scene_backend", "disabled")) != "h3":
        raise RuntimeError("H3 scene backend is not enabled")
    require_authorization(
        "minimax-h3",
        "local-educational-scene-generation",
        C.DEPLOYMENT_TERRITORY,
        path=C.AUTHORIZATION_FILE,
        required_constraints=("territory-reviewed", "no-redistribution"),
    )
    paths = h3_paths()
    required = (paths["python"], paths["root"] / "wgp.py", paths["config"])
    if not all(path.exists() for path in required):
        raise FileNotFoundError("Configured H3 installation is incomplete")
    paths["output"].mkdir(parents=True, exist_ok=True)
    jobs = paths["output"] / ".jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    job_file = jobs / f"{request.name}.json"
    job_file.write_text(json.dumps(h3_job(request), ensure_ascii=False, indent=2), encoding="utf-8")
    command = build_h3_command(request, job_file)
    if dry_run:
        command.append("--dry-run")
    subprocess.run(command, cwd=paths["root"], check=True)
    return paths["output"] / f"{request.name}.mp4"
