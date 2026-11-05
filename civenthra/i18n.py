"""Small, dependency-free locale helpers for public interfaces and prompts."""
from __future__ import annotations

import re


SUPPORTED_LOCALES = ("en", "zh-CN")
_ZH_RE = re.compile(r"[\u3400-\u9fff]")


def normalize_locale(value: str | None, text: str = "") -> str:
    """Return a supported locale; ``auto`` follows the user's text."""
    raw = (value or "auto").strip().replace("_", "-").lower()
    if raw in ("", "auto"):
        return detect_locale(text)
    if raw.startswith("zh"):
        return "zh-CN"
    if raw.startswith("en"):
        return "en"
    raise ValueError("language must be auto, en, or zh-CN")


def detect_locale(text: str) -> str:
    """Prefer Chinese only when CJK characters form a meaningful share."""
    compact = "".join(char for char in text if not char.isspace())
    if not compact:
        return "en"
    chinese = len(_ZH_RE.findall(compact))
    return "zh-CN" if chinese / len(compact) >= 0.15 else "en"
